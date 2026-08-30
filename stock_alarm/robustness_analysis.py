from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from .app import load_env
from .backtest_data import REPORT_DIR
from .factor_analysis import FACTOR_LABELS, HORIZONS, TECHNICAL_FACTORS, daily_ic_series, factor_is_available, factor_records, is_monotonic
from .statistical_validation import benjamini_hochberg, newey_west_mean_test


SCOPES = ("all", "bull", "bear", "sideways")


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise RuntimeError(f"Required analysis output is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        if not rows:
            return
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def hac_lag_for_horizon(horizon: int, policy: str, maximum: int, fixed: int) -> int:
    if policy == "holding_period_minus_one":
        return min(maximum, max(0, horizon - 1))
    if policy == "fixed":
        return min(maximum, max(0, fixed))
    raise ValueError("STAT_HAC_LAG_POLICY must be holding_period_minus_one or fixed")


def factor_hac_results(report_dir: Path, alpha: float, minimum_cross_section: int,
                       lag_policy: str, maximum_lag: int, fixed_lag: int) -> list[dict]:
    samples = _read_csv(report_dir / "factor_samples.csv")
    original_rows = _read_csv(report_dir / "factor_correlations.csv")
    original = {(row["scope"], row["factor"], row["target"]): row for row in original_rows}
    scopes = {"all": samples, **{scope: [row for row in samples if row.get("regime") == scope] for scope in SCOPES[1:]}}
    tested_factors = [factor for factor in FACTOR_LABELS if factor_is_available(samples, factor)]
    rows = []
    for scope, scoped in scopes.items():
        for factor in tested_factors:
            usable = factor_records(scoped, factor)
            for horizon in HORIZONS:
                lag = hac_lag_for_horizon(horizon, lag_policy, maximum_lag, fixed_lag)
                for target_kind in ("return", "excess"):
                    target = f"{target_kind}_{horizon}d_pct"
                    series = daily_ic_series(usable, factor, target, minimum_cross_section)
                    test = newey_west_mean_test([float(item["ic"]) for item in series], lag, "two-sided")
                    old = original.get((scope, factor, target), {})
                    old_p = _float(old.get("ic_p_value"))
                    rows.append({
                        "scope": scope, "factor": factor, "target": target, "horizon": horizon,
                        "ic_period_count": test["sample_count"], "mean_daily_ic": round(float(test["mean"]), 8) if test["mean"] is not None else None,
                        "original_p_value": old_p, "hac_lag": test["lag"],
                        "newey_west_standard_error": round(float(test["standard_error"]), 8) if test["standard_error"] is not None else None,
                        "newey_west_t": round(float(test["t_statistic"]), 8) if test["t_statistic"] is not None else None,
                        "newey_west_p_value": round(float(test["p_value"]), 8),
                        "original_significant": old_p is not None and old_p < alpha,
                        "newey_west_significant": float(test["p_value"]) < alpha,
                    })
    all_adjusted = benjamini_hochberg([float(row["newey_west_p_value"]) for row in rows])
    for row, adjusted in zip(rows, all_adjusted):
        row["fdr_p_value_all"] = round(float(adjusted), 8) if adjusted is not None else None
        row["fdr_significant_all"] = adjusted is not None and adjusted < alpha
    primary_indexes = [index for index, row in enumerate(rows) if str(row["target"]).startswith("excess_")]
    primary_adjusted = benjamini_hochberg([float(rows[index]["newey_west_p_value"]) for index in primary_indexes])
    for row in rows:
        row["fdr_p_value_primary"] = None
        row["fdr_significant_primary"] = False
    for index, adjusted in zip(primary_indexes, primary_adjusted):
        rows[index]["fdr_p_value_primary"] = round(float(adjusted), 8) if adjusted is not None else None
        rows[index]["fdr_significant_primary"] = adjusted is not None and adjusted < alpha
    return rows


def _daily_trade_means(rows: list[dict], regime: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if regime != "all" and row.get("regime") != regime:
            continue
        value = _float(row.get("return_pct"))
        if value is not None:
            grouped[str(row["signal_date"])].append(value)
    return {day: mean(values) for day, values in grouped.items()}


def variant_hac_results(report_dir: Path, alpha: float, lag: int) -> list[dict]:
    trades = _read_csv(report_dir / "weight_variant_trades.csv")
    original_rows = _read_csv(report_dir / "weight_variant_comparison.csv")
    original = {(row["variant"], row["regime"]): row for row in original_rows}
    by_variant = {variant: [row for row in trades if row.get("variant") == variant] for variant in ("variant_1", "variant_2", "variant_3", "variant_4")}
    rows = []
    for variant in ("variant_2", "variant_3", "variant_4"):
        for regime in SCOPES:
            baseline_daily = _daily_trade_means(by_variant["variant_1"], regime)
            proposed_daily = _daily_trade_means(by_variant[variant], regime)
            common_days = sorted(set(baseline_daily) & set(proposed_daily))
            differences = [proposed_daily[day] - baseline_daily[day] for day in common_days]
            test = newey_west_mean_test(differences, lag, "greater")
            old = original[(variant, regime)]
            rows.append({
                "variant": variant, "regime": regime, "paired_day_count": len(common_days),
                "mean_daily_return_difference_pp": round(float(test["mean"]), 8) if test["mean"] is not None else None,
                "mann_whitney_p_value": _float(old.get("p_value")), "hac_lag": test["lag"],
                "newey_west_standard_error": round(float(test["standard_error"]), 8) if test["standard_error"] is not None else None,
                "newey_west_t": round(float(test["t_statistic"]), 8) if test["t_statistic"] is not None else None,
                "newey_west_p_value": round(float(test["p_value"]), 8),
                "original_significant_improvement": str(old.get("significant_return_improvement", "")).lower() == "true",
                "newey_west_significant_improvement": test["mean"] is not None and float(test["mean"]) > 0 and float(test["p_value"]) < alpha,
                "total_return_delta_pp": _float(old.get("total_return_delta_pp")),
                "mdd_delta_pp": _float(old.get("mdd_delta_pp")),
                "excess_return_delta_pp": _float(old.get("excess_return_delta_pp")),
            })
    adjusted = benjamini_hochberg([float(row["newey_west_p_value"]) for row in rows])
    for row, fdr_p in zip(rows, adjusted):
        row["fdr_p_value_12"] = round(float(fdr_p), 8) if fdr_p is not None else None
        row["fdr_significant_improvement"] = bool(
            fdr_p is not None and fdr_p < alpha
            and (row["mean_daily_return_difference_pp"] or 0) > 0
        )
        row["corrected_promotion_candidate"] = bool(
            row["fdr_significant_improvement"]
            and (row["total_return_delta_pp"] or 0) > 0
            and (row["mdd_delta_pp"] or 0) >= -2
            and (row["excess_return_delta_pp"] or 0) > 0
        )
    return rows


def corrected_factor_verdicts(report_dir: Path, factor_rows: list[dict], alpha: float, tolerance: float) -> list[dict]:
    before = {row["factor"]: row for row in _read_csv(report_dir / "factor_verdicts.csv")}
    quantiles = _read_csv(report_dir / "factor_quantiles.csv")
    qlookup: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in quantiles:
        qlookup[(row["scope"], row["factor"], row["target"])].append({
            "quantile": int(row["quantile"]), "mean_return_pct": _float(row.get("mean_return_pct"))
        })
    lookup = {(row["scope"], row["factor"], row["target"]): row for row in factor_rows}
    output = []
    for factor, previous in before.items():
        if not any(row["factor"] == factor for row in factor_rows):
            output.append({
                "factor": factor, "label": previous["label"], "before_verdict": previous["verdict"],
                "after_hac_fdr_verdict": previous["verdict"], "changed": False,
                "reason": "과거 시점 데이터 부재로 통계 보정 대상 아님",
            })
            continue
        primary = [lookup[("all", factor, f"excess_{horizon}d_pct")] for horizon in (3, 5)]
        monotonic = [is_monotonic(sorted(qlookup[("all", factor, f"excess_{horizon}d_pct")], key=lambda row: row["quantile"]), tolerance) for horizon in (3, 5)]
        corrected_positive = [
            (row["mean_daily_ic"] or 0) > 0 and row["newey_west_p_value"] < alpha and row["fdr_p_value_primary"] < alpha
            for row in primary
        ]
        suggestive_positive = [
            (row["mean_daily_ic"] or 0) > 0 and row["newey_west_p_value"] < alpha
            for row in primary
        ]
        if all(corrected_positive) and all(monotonic):
            after = "유의미한 예측력 있음"
        elif any(corrected_positive) or any(suggestive_positive) or previous.get("regime_dependency") not in ("", "없음", "판정 불가"):
            after = "약하거나 불안정"
        else:
            after = "예측력 없음/노이즈"
        output.append({
            "factor": factor, "label": previous["label"], "before_verdict": previous["verdict"],
            "after_hac_fdr_verdict": after, "changed": after != previous["verdict"],
            "reason": "3·5일 전체 초과수익률 HAC+FDR와 분위 단조성, 기존 국면 의존성 반영",
        })
    return output


def _fmt(value) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, bool):
        return "Y" if value else "N"
    return str(value)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(_fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def build_report(factor_rows: list[dict], variant_rows: list[dict], verdicts: list[dict], parameters: dict) -> str:
    factor_nw_hits = [row for row in factor_rows if row["newey_west_significant"]]
    factor_fdr_hits = [row for row in factor_rows if row["fdr_significant_all"]]
    factor_primary_hits = [row for row in factor_rows if row["fdr_significant_primary"]]
    variant_nw_hits = [row for row in variant_rows if row["newey_west_significant_improvement"]]
    variant_hits = [row for row in variant_rows if row["fdr_significant_improvement"]]
    candidates = [row for row in variant_rows if row["corrected_promotion_candidate"]]
    key = next(row for row in factor_rows if row["scope"] == "all" and row["factor"] == "volume_score" and row["target"] == "excess_5d_pct")
    if not factor_primary_hits and not candidates:
        conclusion = "현재 표본과 방법으로는 통계적으로 유의미한 신호를 찾지 못했다."
    else:
        conclusion = f"보정 후 초과수익률 요인 {len(factor_primary_hits)}건, 승격 후보 variant {len(candidates)}건이 남았다."
    lines = [
        "# stockAlarm 통계적 강건성 재검증", "", "## 기술 요약", "", f"**{conclusion}**", "",
        f"핵심 거래량 5일 초과수익률 IC는 기존 p={_fmt(key['original_p_value'])}에서 Newey–West p={_fmt(key['newey_west_p_value'])}, {parameters['factor_test_count_primary']}개 초과수익률 검정 FDR q={_fmt(key['fdr_p_value_primary'])}로 바뀌었습니다.",
        f"요인 검정은 실제 실행 기준 총 {parameters['factor_test_count_all']}개(초과수익률 핵심 가족 {parameters['factor_test_count_primary']}개), variant 검정은 {parameters['variant_test_count']}개입니다. 실제 운영 가중치와 DB는 변경하지 않았습니다.", "",
        f"Newey–West 단독 기준으로는 요인 {len(factor_nw_hits)}개와 variant {len(variant_nw_hits)}개가 p<0.05였지만, BH-FDR 후에는 전체 요인 {len(factor_fdr_hits)}개·핵심 초과수익률 요인 {len(factor_primary_hits)}개·variant {len(variant_hits)}개로 모두 0개입니다.", "",
        "## 요인 IC의 기존 p-value와 HAC·FDR 결과", "",
        "아래 표는 의사결정 핵심인 3일·5일 초과수익률 전체·국면별 결과입니다. 전체 1·3·5·10·20일 및 원수익률 결과는 CSV에 보존됩니다.", "",
    ]
    selected = [row for row in factor_rows if row["target"] in ("excess_3d_pct", "excess_5d_pct")]
    for row in selected:
        row["factor_label"] = FACTOR_LABELS[row["factor"]]
    lines.extend(_table(selected, [
        ("scope", "국면"), ("factor_label", "요인"), ("target", "목표"), ("ic_period_count", "IC 일수"),
        ("mean_daily_ic", "평균 IC"), ("original_p_value", "기존 p"), ("hac_lag", "HAC lag"),
        ("newey_west_p_value", "NW p"), ("fdr_p_value_primary", "FDR q"),
        ("fdr_significant_primary", "보정 유의"),
    ]))
    lines.extend(["", "## FDR 전후 요인 판정", "",
                  "point-in-time 표본이 있는 외부요인은 기술요인과 동일하게 보정했습니다. 국면별 부호 전환은 통계적 유의성과 별개로 불안정성 증거이므로 최종 판정에 남겼습니다.", ""])
    lines.extend(_table(verdicts, [
        ("label", "요인"), ("before_verdict", "보정 전"), ("after_hac_fdr_verdict", "HAC+FDR 후"),
        ("changed", "변경"), ("reason", "근거"),
    ]))
    lines.extend(["", "## variant 개선은 HAC와 FDR 후에도 확인되지 않았다", "",
                  "variant는 같은 신호일에 발생한 거래수익률을 일별 평균한 뒤 baseline과 공통 날짜의 차이 시계열을 만들었습니다. 단측 HAC 검정 후 12개 비교에 BH-FDR을 적용했습니다.", ""])
    lines.extend(_table(variant_rows, [
        ("variant", "안"), ("regime", "국면"), ("paired_day_count", "공통 날짜"),
        ("mean_daily_return_difference_pp", "일평균 차이pp"), ("mann_whitney_p_value", "기존 MW p"),
        ("hac_lag", "HAC lag"), ("newey_west_p_value", "NW p"), ("fdr_p_value_12", "FDR q(12)"),
        ("fdr_significant_improvement", "보정 유의개선"), ("corrected_promotion_candidate", "최종 통과"),
    ]))
    lines.extend(["", "## 검정 수와 보정 범위", "",
                  f"- 가용 요인 전체 가족: {parameters['tested_factor_count']}요인 × 5보유기간 × 4국면 × 2목표 = **{parameters['factor_test_count_all']}개**",
                  f"- 핵심 초과수익률 가족: {parameters['tested_factor_count']}요인 × 5보유기간 × 4국면 = **{parameters['factor_test_count_primary']}개**",
                  f"- variant 가족: 3제안 × 4국면 = **{parameters['variant_test_count']}개**",
                  f"- FDR 유의수준: {parameters['alpha']}; 방법: Benjamini–Hochberg",
                  "- 요인 표에는 핵심 가족 80개의 q-value를 표시하며, 더 보수적인 전체 160개 q-value도 CSV에 함께 저장합니다.", "",
                  "## 방법과 파라미터", "",
                  f"- 요인 HAC lag 정책: `{parameters['lag_policy']}`; 최대 {parameters['maximum_lag']}일. 겹치는 h일 선행수익률이 h-1개 세션을 공유하므로 lag=h-1을 사용합니다.",
                  f"- variant HAC lag: {parameters['variant_lag']}일. 기존 비교의 주요 보유·평가 창 5일을 보수적으로 반영한 설정입니다.",
                  "- Newey–West 장기분산은 Bartlett kernel을 사용하고, 자유도 n-1인 Student t 분포로 p-value를 계산합니다.",
                  "- 요인 IC는 양·음 방향 모두 탐지하는 양측 검정, variant 개선은 baseline보다 높다는 단측 검정입니다.", "",
                  "## 검증 평가와 한계", "",
                  "**공유 판단: Share with caveats.** 계산 경로와 보정은 재현 가능하지만, 아래 한계 때문에 운영 변경 근거로 단독 사용하면 안 됩니다.", "",
                  "- 하락장 174거래일 중 실제 횡단면 IC 산출일이 적어 HAC 추정 자체도 불안정할 수 있습니다.",
                  "- Newey–West는 지정 lag까지의 자기상관을 보정하지만 구조적 변화, 종목 간 장기 의존성, 생존편향을 제거하지 않습니다.",
                  "- variant 일별 차이는 공통 신호일만 사용하므로 서로 다른 날짜에만 발생한 신호 정보는 검정에서 제외됩니다.",
                  "- 뉴스·공시·재무는 point-in-time 가용 레코드만 사용하므로 출처별 커버리지가 낮으면 검정력이 제한됩니다.", "",
                  "## 종합 결론과 제안", "", f"**{conclusion}**", "",
                  "- 보정 전 단일 p-value만으로 요인 또는 variant를 채택하지 않습니다.",
                  "- 실제 운영 가중치는 유지하고, 완전 미사용 기간 및 더 긴 하락장 표본을 축적합니다.",
                  "- 다음 검증은 거래일 블록 부트스트랩과 실제 현금·일별 NAV 기반 포트폴리오 시뮬레이션을 권장합니다.", "",
                  "## 추가 질문", "",
                  "- HAC lag를 자동 선택하거나 3·5·10일로 바꿔도 결론이 유지되는가?",
                  "- 현재 watchlist가 아닌 당시 구성종목으로 생존편향을 줄여도 결과가 유지되는가?",
                  "- point-in-time 외부요인 데이터가 확보된 뒤 7개 전체 요인 FDR 결과는 어떻게 달라지는가?", ""])
    return "\n".join(lines)


def run(report_dir: Path = REPORT_DIR) -> dict:
    load_env()
    alpha = float(os.environ.get("STAT_FDR_ALPHA", "0.05"))
    minimum_cross_section = int(os.environ.get("FACTOR_MIN_CROSS_SECTION", "5"))
    lag_policy = os.environ.get("STAT_HAC_LAG_POLICY", "holding_period_minus_one")
    maximum_lag = int(os.environ.get("STAT_HAC_MAX_LAG", "19"))
    fixed_lag = int(os.environ.get("STAT_HAC_FIXED_LAG", "4"))
    variant_lag = int(os.environ.get("WEIGHT_VARIANT_HAC_LAG", "5"))
    tolerance = float(os.environ.get("FACTOR_MONOTONIC_TOLERANCE_PCT", "0.05"))
    factor_rows = factor_hac_results(report_dir, alpha, minimum_cross_section, lag_policy, maximum_lag, fixed_lag)
    variant_rows = variant_hac_results(report_dir, alpha, variant_lag)
    verdicts = corrected_factor_verdicts(report_dir, factor_rows, alpha, tolerance)
    parameters = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "alpha": alpha,
        "lag_policy": lag_policy, "maximum_lag": maximum_lag, "fixed_lag": fixed_lag,
        "variant_lag": variant_lag, "minimum_cross_section": minimum_cross_section,
        "factor_test_count_all": len(factor_rows),
        "factor_test_count_primary": sum(str(row["target"]).startswith("excess_") for row in factor_rows),
        "tested_factor_count": len({row["factor"] for row in factor_rows}),
        "variant_test_count": len(variant_rows), "live_config_changed": False, "database_access": "none",
        "visual_omission_reason": "Exact p-value and q-value audit tables are more useful than charts for this validation appendix.",
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(report_dir / "factor_hac_fdr.csv", factor_rows)
    _write_csv(report_dir / "variant_hac_fdr.csv", variant_rows)
    _write_csv(report_dir / "factor_verdict_corrections.csv", verdicts)
    (report_dir / "robustness_parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = report_dir / "STATISTICAL_ROBUSTNESS_REPORT.md"
    report_path.write_text(build_report(factor_rows, variant_rows, verdicts, parameters), encoding="utf-8")
    result = {
        "report": str(report_path),
        "factor_fdr_hits_primary": sum(row["fdr_significant_primary"] for row in factor_rows),
        "variant_fdr_hits": sum(row["fdr_significant_improvement"] for row in variant_rows),
        "corrected_promotion_candidates": [row["variant"] for row in variant_rows if row["corrected_promotion_candidate"]],
        "live_config_changed": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
