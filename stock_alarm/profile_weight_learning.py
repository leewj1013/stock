from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from .app import load_env
from .backtest_data import DATA_DIR, REPORT_DIR
from .strategy_learning import _correlation, _fold_summary, cliffs_delta, return_distribution_p_value
from .trading_profiles import CATEGORY_VALUE_KEYS, PROFILES
from .validation_backtest import BacktestEngine

CATEGORIES = tuple(CATEGORY_VALUE_KEYS)


def _proposed_weights(training: list[tuple[dict, float]], current: dict[str, float], max_change: float = 0.05) -> dict[str, float]:
    """Nudge each category's weight toward how well it actually correlated
    with the realized return in `training`, capped to `max_change` per call --
    same shape as strategy_learning._proposed_weights, just over the 6
    profile categories instead of the 7 global factors, and renormalized so
    the weights keep summing to 1 (profile_total_score expects that)."""
    default = 1 / len(CATEGORIES)
    raw = {}
    for category in CATEGORIES:
        xs = [row.get(category, 0.0) for row, _value in training]
        ys = [value for _row, value in training]
        current_weight = current.get(category, default)
        target = max(0.0, current_weight + _correlation(xs, ys) * 0.05)
        raw[category] = max(current_weight - max_change, min(current_weight + max_change, target))
    total = sum(raw.values()) or 1.0
    return {category: round(value / total, 4) for category, value in raw.items()}


def _weighted_score(categories: dict, weights: dict[str, float]) -> float:
    return sum(categories.get(category, 0.0) * weights.get(category, 0.0) for category in CATEGORIES)


def _ranked_returns(validation: list[tuple[dict, float]], weights: dict[str, float], top_fraction: float = 0.5) -> list[float]:
    scored = sorted(((_weighted_score(row, weights), value) for row, value in validation), reverse=True)
    cutoff = max(1, int(len(scored) * top_fraction))
    return [value for _score, value in scored[:cutoff]]


def learn_profile_weights(
    rows: list[tuple[str, dict, float]], current: dict[str, float],
    fold_count: int = 4, validation_size: int = 60, alpha: float = 0.05, max_change: float = 0.05,
) -> dict:
    """Walk-forward, correlation-based learning of category weights, reusing
    the exact fold pass/fail criteria strategy_learning.walk_forward_validate
    uses for the live global factor weights: return improved, MDD within
    2pp, and statistically significant, each fold trained only on data
    strictly before it. Unlike that live learner this never writes anything
    back automatically -- profile_weight_validation.py already showed the
    hand-picked category weights aren't fold-robust yet, so this is a report
    to inform a manual PROFILES update, not an auto-promotion pipeline."""
    ordered = sorted(rows, key=lambda item: item[0])
    usable = [(categories, value) for _day, categories, value in ordered]
    required = validation_size * fold_count + 1
    if len(usable) < required:
        return {"status": "insufficient_data", "sample_count": len(usable), "minimum": required, "folds": [], "weights": dict(current)}
    validation_start = len(usable) - validation_size * fold_count
    folds = []
    all_baseline: list[float] = []
    all_proposed: list[float] = []
    for fold_index in range(fold_count):
        start = validation_start + fold_index * validation_size
        stop = start + validation_size
        training, validation = usable[:start], usable[start:stop]
        fold_weights = _proposed_weights(training, current, max_change)
        baseline_values = _ranked_returns(validation, current)
        proposed_values = _ranked_returns(validation, fold_weights)
        summary = _fold_summary(fold_index + 1, baseline_values, proposed_values, alpha, len(validation))
        summary["effect_size"] = cliffs_delta(proposed_values, baseline_values)
        folds.append(summary)
        all_baseline.extend(baseline_values)
        all_proposed.extend(proposed_values)
    proposed = _proposed_weights(usable, current, max_change)
    p_value = return_distribution_p_value(all_proposed, all_baseline)
    accepted = all(fold["passed"] for fold in folds) and p_value < alpha
    return {
        "status": "learned_and_fold_robust" if accepted else "learned_but_not_fold_robust",
        "sample_count": len(usable), "weights": proposed, "current_weights": dict(current), "folds": folds,
        "overall_p_value": p_value, "overall_effect_size": cliffs_delta(all_proposed, all_baseline),
        "baseline_avg_return_pct": round(mean(all_baseline), 4) if all_baseline else None,
        "proposed_avg_return_pct": round(mean(all_proposed), 4) if all_proposed else None,
    }


def _fmt(value) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def build_report(results: dict[str, dict]) -> str:
    lines = ["# stockAlarm 프로필 카테고리 가중치 학습 결과", "", "## 요약", "",
             "각 프로필의 현재 고정 가중치 대신, 실제 카테고리 점수와 이후 5일 초과수익률의 상관관계로 가중치를 재추정한 뒤 "
             "동일한 달력 기반 walk-forward 폴드로 검증한 결과입니다. 자동으로 trading_profiles.py에 반영되지 않습니다 -- "
             "학습된 가중치가 폴드 전반에서 일관되게 낫다고 나오지 않는 한 수동 적용은 권장하지 않습니다.", ""]
    for name, result in results.items():
        if result["status"] == "insufficient_data":
            lines.append(f"**{name}**: 표본 부족 ({result['sample_count']}/{result['minimum']}건) -- 기존 가중치 유지")
            continue
        verdict = "폴드 전반에서 일관되게 개선 (수동 적용 검토 가능)" if result["status"] == "learned_and_fold_robust" else "폴드 전반에서 일관성 부족 -- 기존 가중치 유지 권장"
        lines.append(
            f"**{name}**: {verdict} (표본 {result['sample_count']}건, 전체 p-value={result['overall_p_value']:.4f}, "
            f"effect size={result['overall_effect_size']:+.4f})"
        )
    for name, result in results.items():
        if result["status"] == "insufficient_data":
            continue
        lines.extend(["", f"## {name}: 현재 vs 학습된 가중치", ""])
        lines.extend([
            "| 카테고리 | 현재 | 학습됨 |", "|---|---|---|",
            *[f"| {category} | {result['current_weights'].get(category, 0):.4f} | {result['weights'].get(category, 0):.4f} |" for category in CATEGORIES],
        ])
        lines.extend(["", f"## {name}: 구간별 결과", ""])
        lines.extend([
            "| 폴드 | 표본 | baseline 평균% | 학습됨 평균% | p-value | effect size | 통과 |",
            "|---|---|---|---|---|---|---|",
            *[
                f"| {fold['fold']} | {fold['sample_count']} | {_fmt(fold['baseline_return'])} | {_fmt(fold['proposed_return'])} | "
                f"{_fmt(fold['p_value'])} | {_fmt(fold['effect_size'])} | {_fmt(fold['passed'])} |"
                for fold in result["folds"]
            ],
        ])
    return "\n".join(lines) + "\n"


def run(fold_count: int = 4, validation_size: int = 60, report_dir: Path = REPORT_DIR) -> dict:
    load_env()
    # category_training_rows() doesn't depend on which profile is active (it
    # walks every day's shared, quality-filtered candidate pool) -- compute
    # it once and reuse for every profile's own current weights instead of
    # re-running the whole multi-year evaluation per profile.
    rows = BacktestEngine(DATA_DIR, report_dir).category_training_rows()
    results = {
        name: learn_profile_weights(rows, profile["scoring_weights"], fold_count=fold_count, validation_size=validation_size)
        for name, profile in PROFILES.items()
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "PROFILE_WEIGHT_LEARNING.md").write_text(build_report(results), encoding="utf-8")
    (report_dir / "profile_weight_learning.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"report": str(report_dir / "PROFILE_WEIGHT_LEARNING.md"), "results": {name: result["status"] for name, result in results.items()}}
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    run()
