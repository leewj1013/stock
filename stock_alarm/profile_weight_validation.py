from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

from .app import load_env
from .backtest_data import DATA_DIR, REPORT_DIR
from .strategy_learning import _fold_summary, cliffs_delta, return_distribution_p_value
from .trading_profiles import PROFILES
from .validation_backtest import BacktestEngine, Trade


def _fold_boundaries(trades: list[Trade], fold_count: int) -> list[date]:
    """Fixed calendar-date cut points shared by every variant, so "fold 1"
    means the same period for baseline and the profile being compared --
    folding each variant's own trades independently would misalign the
    periods since trade counts differ a lot between variants."""
    exit_dates = sorted(date.fromisoformat(trade.exit_date) for trade in trades)
    start, end = exit_dates[0], exit_dates[-1] + timedelta(days=1)
    step = max(1, (end - start).days // fold_count)
    boundaries = [start + timedelta(days=step * index) for index in range(fold_count)]
    boundaries.append(end)
    return boundaries


def _bucket_by_fold(trades: list[Trade], boundaries: list[date]) -> list[list[Trade]]:
    folds: list[list[Trade]] = [[] for _ in range(len(boundaries) - 1)]
    for trade in trades:
        exit_date = date.fromisoformat(trade.exit_date)
        for index in range(len(boundaries) - 1):
            if boundaries[index] <= exit_date < boundaries[index + 1]:
                folds[index].append(trade)
                break
    return folds


def validate_profile(
    profile_name: str, fold_count: int = 4, alpha: float = 0.05,
    baseline_trades: list[Trade] | None = None, data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR,
) -> dict:
    """Chronological-fold robustness check for one profile's category
    weights against the shared baseline ranking, reusing the exact
    per-fold pass/fail criteria (return improved, MDD within 2pp,
    statistically significant) the live weight-learning promotion gate
    uses -- see strategy_learning.walk_forward_validate. The weights here
    aren't fit per fold (they're fixed, hand-specified), so this measures
    whether the improvement holds up consistently across sub-periods
    rather than a single lucky pass over the whole history.
    """
    if baseline_trades is None:
        baseline_engine = BacktestEngine(data_dir, report_dir, profile=None)
        baseline_trades, _ = baseline_engine.run(partial_profit=True)
    profile = PROFILES[profile_name]
    profile_engine = BacktestEngine(data_dir, report_dir, profile=profile)
    profile_trades, _ = profile_engine.run(partial_profit=True)

    boundaries = _fold_boundaries(baseline_trades, fold_count)
    baseline_folds = _bucket_by_fold(baseline_trades, boundaries)
    profile_folds = _bucket_by_fold(profile_trades, boundaries)

    folds = []
    for index in range(fold_count):
        baseline_values = [trade.return_pct for trade in baseline_folds[index]]
        proposed_values = [trade.return_pct for trade in profile_folds[index]]
        if not baseline_values or not proposed_values:
            folds.append({
                "fold": index + 1, "period_start": boundaries[index].isoformat(), "period_end": boundaries[index + 1].isoformat(),
                "sample_count": len(proposed_values), "passed": False, "decision_reason": "insufficient_samples_in_fold",
            })
            continue
        summary = _fold_summary(index + 1, baseline_values, proposed_values, alpha)
        summary["period_start"], summary["period_end"] = boundaries[index].isoformat(), boundaries[index + 1].isoformat()
        summary["effect_size"] = cliffs_delta(proposed_values, baseline_values)
        folds.append(summary)

    all_baseline = [trade.return_pct for trade in baseline_trades]
    all_proposed = [trade.return_pct for trade in profile_trades]
    p_value = return_distribution_p_value(all_proposed, all_baseline)
    effect_size = cliffs_delta(all_proposed, all_baseline)
    folds_passed = sum(bool(fold.get("passed")) for fold in folds)
    accepted = folds_passed == fold_count and p_value < alpha
    # "consistent_across_folds" is the strict walk-forward promotion-style gate
    # (every sub-period must improve AND be significant) -- with only ~4
    # folds of a few hundred trades each, that bar is hard to clear even when
    # there's a real, small effect. relaxed_status reports the simpler
    # whole-period comparison as a second, less strict lens on the same data.
    relaxed_accepted = p_value < alpha and mean(all_proposed) > mean(all_baseline) if all_proposed and all_baseline else False
    relaxed_status = "directionally_better_and_significant_overall" if relaxed_accepted else "no_significant_overall_improvement"
    return {
        "profile": profile_name, "fold_count": fold_count, "folds_passed": folds_passed,
        "overall_avg_return_pct": round(mean(all_proposed), 4) if all_proposed else None,
        "baseline_avg_return_pct": round(mean(all_baseline), 4) if all_baseline else None,
        "overall_p_value": p_value, "overall_effect_size": effect_size,
        "status": "consistent_across_folds" if accepted else "inconsistent_or_not_significant",
        "relaxed_status": relaxed_status,
        "folds": folds,
    }


def _fmt(value) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(_fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def build_report(results: list[dict]) -> str:
    lines = ["# stockAlarm 프로필 가중치 국면 분리 검증", "", "## 기술 요약", ""]
    for result in results:
        verdict = "일관되게 개선 (모든 폴드 통과 + 전체 유의)" if result["status"] == "consistent_across_folds" else "일관성 부족 또는 유의성 미달"
        relaxed = "전체 기간 기준으로는 유의미하게 개선" if result.get("relaxed_status") == "directionally_better_and_significant_overall" else "전체 기간 기준으로도 유의미한 개선 없음"
        lines.append(
            f"**{result['profile']}**: {verdict} ({result['folds_passed']}/{result['fold_count']} 폴드 통과, "
            f"전체 p-value={result['overall_p_value']:.4f}, effect size(Cliff's delta)={result.get('overall_effect_size', 0):+.4f}) "
            f"-- 완화 기준: {relaxed}"
        )
    lines.append("")
    lines.append(
        "baseline은 가중치 없이 기존 품질점수로만 순위 매긴 안입니다. 각 폴드는 baseline과 동일한 달력 구간으로 나눴습니다 (거래 수가 아니라 날짜 기준). "
        "effect size는 Cliff's delta -- profile 거래가 baseline 거래보다 나을 확률에서 나쁠 확률을 뺀 값으로, 0에 가까우면 p-value가 작아도 실질적 차이는 미미하다는 뜻입니다. "
        "'통과' 열의 엄격 기준(모든 폴드 통과)과 별개로, 완화 기준은 전체 기간을 하나로 묶어 유의성만 봅니다 (표본이 적은 개별 폴드에서는 유의성이 나오기 어렵기 때문)."
    )
    for result in results:
        lines.extend(["", f"## {result['profile']}: 구간별 결과", ""])
        lines.extend(_table(result["folds"], [
            ("fold", "폴드"), ("period_start", "시작"), ("period_end", "종료"), ("sample_count", "표본"),
            ("baseline_return", "baseline 평균%"), ("proposed_return", "profile 평균%"),
            ("p_value", "p-value"), ("effect_size", "effect size"), ("passed", "통과"), ("decision_reason", "미통과 사유"),
        ]))
    return "\n".join(lines) + "\n"


def run(fold_count: int = 4, report_dir: Path = REPORT_DIR) -> dict:
    load_env()
    alpha = float(os.environ.get("LEARNING_SIGNIFICANCE_LEVEL", "0.05"))
    baseline_engine = BacktestEngine(DATA_DIR, report_dir)
    baseline_trades, _ = baseline_engine.run(partial_profit=True)
    results = [
        validate_profile(name, fold_count=fold_count, alpha=alpha, baseline_trades=baseline_trades)
        for name, profile in PROFILES.items() if not profile.get("comparison_only")
    ]
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "PROFILE_WEIGHT_VALIDATION.md").write_text(build_report(results), encoding="utf-8")
    (report_dir / "profile_weight_validation.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"report": str(report_dir / "PROFILE_WEIGHT_VALIDATION.md"),
               "results": {result["profile"]: result["status"] for result in results}}
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    run()
