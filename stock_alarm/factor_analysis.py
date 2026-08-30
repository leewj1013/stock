from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

from .app import load_env
from .backtest_data import BENCHMARK, DATA_DIR, REPORT_DIR
from .strategy_learning import FACTORS
from .validation_backtest import BacktestEngine


HORIZONS = (1, 3, 5, 10, 20)
TECHNICAL_FACTORS = {"volume_score", "trading_value_score", "trend_score", "relative_strength_score"}
POINT_IN_TIME_FACTORS = {"news_score", "disclosure_score", "financial_score"}
FACTOR_LABELS = {
    "volume_score": "거래량", "trading_value_score": "거래대금", "trend_score": "추세",
    "relative_strength_score": "상대강도", "news_score": "뉴스", "disclosure_score": "공시",
    "financial_score": "재무",
}
CONFIGURED_POINTS = {
    "volume_score": "40", "trading_value_score": "30", "trend_score": "30",
    "relative_strength_score": "±5", "financial_score": "최대 5",
}


def configured_points(factor: str) -> str:
    if factor == "news_score":
        return f"원자점수×{os.environ.get('NEWS_SCORE_WEIGHT', '0')}"
    if factor == "disclosure_score":
        return f"원자점수×{os.environ.get('DART_SCORE_WEIGHT', '0')}"
    return CONFIGURED_POINTS[factor]


def _number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def average_ranks(values: list[float]) -> list[float]:
    """Return one-based average ranks, preserving ties."""
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2
        for index in ordered[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    xbar, ybar = mean(xs), mean(ys)
    numerator = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - xbar) ** 2 for x in xs) * sum((y - ybar) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def _two_sided_normal_p(z: float) -> float:
    return min(1.0, math.erfc(abs(z) / math.sqrt(2)))


def spearman_correlation(xs: list[float], ys: list[float]) -> tuple[float | None, float | None, int]:
    """Spearman rho with an asymptotic two-sided p-value.

    The report's promotion-style verdict does not use this pooled p-value; it
    uses the daily cross-sectional IC series so observations on the same market
    date are not treated as independent evidence.
    """
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if _number(x) is not None and _number(y) is not None]
    if len(pairs) < 3:
        return None, None, len(pairs)
    rho = _pearson(average_ranks([x for x, _ in pairs]), average_ranks([y for _, y in pairs]))
    if rho is None:
        return None, None, len(pairs)
    if abs(rho) >= 1:
        return rho, 0.0, len(pairs)
    z = rho * math.sqrt((len(pairs) - 2) / max(1e-12, 1 - rho * rho))
    return rho, _two_sided_normal_p(z), len(pairs)


def extract_evaluation_factors(evaluation) -> dict[str, float]:
    """Expose exactly the factor values produced by the common live evaluator."""
    return {factor: float(evaluation.values.get(factor) or 0) for factor in FACTORS}


def daily_ic_series(records: list[dict], factor: str, target: str, minimum_cross_section: int = 5) -> list[dict]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        if _number(row.get(factor)) is not None and _number(row.get(target)) is not None:
            by_day[str(row["signal_date"])].append(row)
    daily = []
    for day, day_rows in sorted(by_day.items()):
        if len(day_rows) < minimum_cross_section:
            continue
        rho, _p, _n = spearman_correlation(
            [float(row[factor]) for row in day_rows], [float(row[target]) for row in day_rows]
        )
        if rho is not None:
            daily.append({"signal_date": day, "ic": rho, "cross_section_count": len(day_rows)})
    return daily


def daily_information_coefficient(records: list[dict], factor: str, target: str, minimum_cross_section: int = 5) -> dict:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        if _number(row.get(factor)) is not None and _number(row.get(target)) is not None:
            by_day[str(row["signal_date"])].append(row)
    daily_rows = daily_ic_series(records, factor, target, minimum_cross_section)
    daily = [float(row["ic"]) for row in daily_rows]
    pooled_rows = [row for rows in by_day.values() for row in rows]
    pooled_rho, pooled_p, case_count = spearman_correlation(
        [float(row[factor]) for row in pooled_rows], [float(row[target]) for row in pooled_rows]
    ) if pooled_rows else (None, None, 0)
    if not daily:
        return {"case_count": case_count, "period_count": 0, "mean_daily_ic": None, "ic_p_value": None,
                "pooled_rho": pooled_rho, "pooled_p_value": pooled_p}
    ic_mean = mean(daily)
    deviation = pstdev(daily)
    if len(daily) < 2:
        ic_p = None
    elif deviation == 0:
        ic_p = 0.0 if ic_mean else 1.0
    else:
        ic_p = _two_sided_normal_p(ic_mean / (deviation / math.sqrt(len(daily))))
    return {
        "case_count": case_count, "period_count": len(daily), "mean_daily_ic": ic_mean,
        "ic_p_value": ic_p, "pooled_rho": pooled_rho, "pooled_p_value": pooled_p,
    }


def cross_sectional_quantiles(records: list[dict], factor: str, target: str, quantiles: int = 5) -> list[dict]:
    """Create quantiles within each signal date, then aggregate future returns."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        if _number(row.get(factor)) is not None and _number(row.get(target)) is not None:
            by_day[str(row["signal_date"])].append(row)
    buckets: dict[int, list[float]] = defaultdict(list)
    days: dict[int, set[str]] = defaultdict(set)
    for day, day_rows in by_day.items():
        if len(day_rows) < quantiles:
            continue
        factor_values = [float(row[factor]) for row in day_rows]
        if min(factor_values) == max(factor_values):
            continue
        ranks = average_ranks(factor_values)
        for row, rank in zip(day_rows, ranks):
            bucket = min(quantiles, int((rank - 1) * quantiles / len(day_rows)) + 1)
            buckets[bucket].append(float(row[target]))
            days[bucket].add(day)
    return [
        {"quantile": bucket, "sample_count": len(buckets.get(bucket, [])),
         "period_count": len(days.get(bucket, set())),
         "mean_return_pct": mean(buckets[bucket]) if buckets.get(bucket) else None}
        for bucket in range(1, quantiles + 1)
    ]


def is_monotonic(quantile_rows: list[dict], tolerance_pct: float = 0.05) -> bool:
    values = [row.get("mean_return_pct") for row in quantile_rows]
    if not values or any(value is None for value in values):
        return False
    return values[-1] > values[0] and all(right + tolerance_pct >= left for left, right in zip(values, values[1:]))


def extract_factor_samples(engine: BacktestEngine) -> list[dict]:
    rows = []
    benchmark_days = sorted(day for day in engine.regimes if day in engine.by_date.get(BENCHMARK, {}))
    for day in benchmark_days:
        for evaluation in engine.passed_evaluations(day):
            outcomes = engine.forward_outcomes(evaluation.ticker, day, HORIZONS)
            if not outcomes:
                continue
            rows.append({
                "signal_date": day, "ticker": evaluation.ticker, "name": evaluation.name,
                "regime": engine.regimes.get(day, "unclassified"),
                "external_factor_status": evaluation.values.get("external_factor_status", "unavailable_point_in_time_snapshot"),
                "pit_news_available": bool(evaluation.values.get("pit_news_available", False)),
                "pit_disclosure_available": bool(evaluation.values.get("pit_disclosure_available", False)),
                "pit_financial_available": bool(evaluation.values.get("pit_financial_available", False)),
                **extract_evaluation_factors(evaluation), **outcomes,
            })
    return rows


def _scope_records(records: list[dict]) -> dict[str, list[dict]]:
    return {"all": records, **{regime: [row for row in records if row.get("regime") == regime] for regime in ("bull", "bear", "sideways")}}


def factor_is_available(records: list[dict], factor: str) -> bool:
    if factor in TECHNICAL_FACTORS:
        return True
    source = factor.removesuffix("_score")
    return any(str(row.get(f"pit_{source}_available", "")).lower() in ("true", "1") for row in records)


def factor_records(records: list[dict], factor: str) -> list[dict]:
    if factor in TECHNICAL_FACTORS:
        return records
    source = factor.removesuffix("_score")
    return [row for row in records if str(row.get(f"pit_{source}_available", "")).lower() in ("true", "1")]


def row_has_factor(row: dict, factor: str) -> bool:
    return factor in TECHNICAL_FACTORS or str(row.get(f"pit_{factor.removesuffix('_score')}_available", "")).lower() in ("true", "1")


def analyze(records: list[dict], alpha: float, quantiles: int, minimum_cross_section: int,
            monotonic_tolerance: float, regime_day_counts: dict[str, int], limited_days: int) -> dict:
    scopes = _scope_records(records)
    correlations, quantile_output = [], []
    for scope, scoped in scopes.items():
        for factor in FACTORS:
            usable = factor_records(scoped, factor)
            for horizon in HORIZONS:
                for kind in ("return", "excess"):
                    target = f"{kind}_{horizon}d_pct"
                    metric = daily_information_coefficient(usable, factor, target, minimum_cross_section)
                    correlations.append({
                        "scope": scope, "factor": factor, "target": target,
                        "source_status": "available" if factor_is_available(records, factor) else "unavailable_point_in_time_snapshot",
                        "sample_warning": "표본 제한 주의" if scope != "all" and regime_day_counts.get(scope, 0) < limited_days else "",
                        **{key: None if value is None else round(value, 8) if isinstance(value, float) else value for key, value in metric.items()},
                    })
                for target in (f"excess_{horizon}d_pct",):
                    qrows = cross_sectional_quantiles(usable, factor, target, quantiles)
                    for row in qrows:
                        quantile_output.append({
                            "scope": scope, "factor": factor, "target": target,
                            **{key: round(value, 8) if isinstance(value, float) else value for key, value in row.items()},
                        })

    matrix = []
    for left in FACTORS:
        row = {"factor": left}
        for right in FACTORS:
            usable = [item for item in records if row_has_factor(item, left) and row_has_factor(item, right)]
            rho, _p, _n = spearman_correlation(
                [float(item[left]) for item in usable], [float(item[right]) for item in usable]
            ) if usable else (None, None, 0)
            row[right] = None if rho is None else round(rho, 6)
        matrix.append(row)

    lookup = {(row["scope"], row["factor"], row["target"]): row for row in correlations}
    qlookup: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in quantile_output:
        qlookup[(row["scope"], row["factor"], row["target"])].append(row)
    verdicts = []
    for factor in FACTORS:
        if not factor_is_available(records, factor):
            verdicts.append({
                "factor": factor, "label": FACTOR_LABELS[factor], "configured_points": configured_points(factor),
                "predictive_score": None, "predictive_rank": None, "verdict": "검증 불가(시점 데이터 없음)",
                "regime_dependency": "판정 불가", "proposal": "과거 시점 스냅샷 적재 후 재검증",
            })
            continue
        key_metrics = [lookup[("all", factor, f"excess_{days}d_pct")] for days in (3, 5)]
        monotonic_flags = [is_monotonic(qlookup[("all", factor, f"excess_{days}d_pct")], monotonic_tolerance) for days in (3, 5)]
        strong = all((row["mean_daily_ic"] or 0) > 0 and row["ic_p_value"] is not None and row["ic_p_value"] < alpha for row in key_metrics) and all(monotonic_flags)
        strong_regimes, regime_signs = [], []
        for regime in ("bull", "bear", "sideways"):
            metrics = [lookup[(regime, factor, f"excess_{days}d_pct")] for days in (3, 5)]
            if all((row["mean_daily_ic"] or 0) > 0 and row["ic_p_value"] is not None and row["ic_p_value"] < alpha for row in metrics):
                strong_regimes.append(regime)
            regime_signs.extend(1 if (row["mean_daily_ic"] or 0) > 0.01 else -1 if (row["mean_daily_ic"] or 0) < -0.01 else 0 for row in metrics)
        significant_any = any(row["ic_p_value"] is not None and row["ic_p_value"] < alpha for row in key_metrics)
        regime_dependent = bool(strong_regimes) and len(strong_regimes) < 3 or (1 in regime_signs and -1 in regime_signs)
        if strong:
            verdict, proposal = "유의미한 예측력 있음", "유지 검토"
        elif significant_any or any(monotonic_flags) or regime_dependent:
            verdict, proposal = "약하거나 불안정", "축소 또는 국면별 적용 검토"
        else:
            verdict, proposal = "예측력 없음/노이즈", "축소·제거 검토"
        predictive_score = mean(float(row["mean_daily_ic"] or 0) for row in key_metrics)
        verdicts.append({
            "factor": factor, "label": FACTOR_LABELS[factor], "configured_points": configured_points(factor),
            "predictive_score": round(predictive_score, 8), "predictive_rank": None, "verdict": verdict,
            "regime_dependency": (",".join(strong_regimes) or "국면별 부호 전환") if regime_dependent else "없음",
            "proposal": proposal,
        })
    ranked = sorted((row for row in verdicts if row["predictive_score"] is not None), key=lambda row: row["predictive_score"], reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["predictive_rank"] = rank
    return {"correlations": correlations, "quantiles": quantile_output, "matrix": matrix, "verdicts": verdicts}


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        if not columns:
            return
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value, digits: int = 4) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(_fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def build_report(records: list[dict], results: dict, parameters: dict, manifest: dict, quality_rows: list[dict]) -> str:
    regime_counts = parameters["regime_day_counts"]
    verdicts = results["verdicts"]
    available = [row for row in verdicts if row["predictive_score"] is not None]
    unavailable = [row["label"] for row in verdicts if row["predictive_score"] is None]
    lines = [
        "# stockAlarm 요인별 예측력 검증", "",
        "## 기술 요약", "",
        f"분석 대상은 매수 필수조건과 시장 필터를 통과한 **{len(records):,}건**이며, 판정은 종목별 관측치를 그대로 독립 취급하지 않고 일별 횡단면 스피어만 IC의 시계열을 사용했습니다.",
        f"검증 가능한 기술 요인 중 3·5일 초과수익률 평균 IC 기준 1위는 **{available[0]['label'] if available else '없음'}**입니다. 아래 판정은 가중치 변경 명령이 아니라 검토 제안입니다.",
        (f"**{', '.join(unavailable)}** 요인은 충분한 과거 시점 스냅샷이 없어 검증할 수 없습니다. 0점 결과를 예측력 없음으로 해석하면 안 됩니다." if unavailable else "뉴스·공시·재무를 포함한 7개 요인은 공개시각 기준 point-in-time 표본만 사용했습니다."), "",
        "## 가중치와 실제 예측력은 일치하는가", "",
        "예측력 점수는 전체 기간의 3일·5일 벤치마크 대비 초과수익률에 대한 평균 일별 IC입니다. 양수일수록 높은 요인값이 더 높은 미래 초과수익률과 연결됐다는 뜻입니다.", "",
    ]
    lines.extend(_table(verdicts, [
        ("label", "요인"), ("configured_points", "현재 점수 배분"), ("predictive_score", "3·5일 평균 IC"),
        ("predictive_rank", "예측력 순위"), ("verdict", "판정"), ("regime_dependency", "국면 의존"), ("proposal", "검토 제안"),
    ]))
    lines.extend(["", "## 전체와 국면별 3·5일 초과수익률 IC", "",
                  "`mean_daily_ic`의 유의확률(`ic_p_value`)을 주 판정에 사용합니다. `pooled_rho`는 감사용 참고값이며 같은 날짜 종목 간 상관 때문에 그 p-value를 판정에 쓰지 않습니다.", ""])
    selected_corr = [row for row in results["correlations"] if row["target"] in ("excess_3d_pct", "excess_5d_pct")]
    selected_corr = [row for row in selected_corr if row["source_status"] == "available"]
    for row in selected_corr:
        row["factor_label"] = FACTOR_LABELS[row["factor"]]
    lines.extend(_table(selected_corr, [
        ("scope", "구간"), ("factor_label", "요인"), ("target", "목표"), ("case_count", "사례 수"),
        ("period_count", "IC 일수"), ("mean_daily_ic", "평균 일별 IC"), ("ic_p_value", "IC p-value"),
        ("pooled_rho", "전체 관측 rho"), ("sample_warning", "주의"),
    ]))
    lines.extend(["", "## 점수가 높을수록 수익률도 순차적으로 높아지는가", "",
                  "각 거래일 안에서 요인을 5분위로 나눈 뒤 분위별 평균 초과수익률을 합산했습니다. 아래는 의사결정의 핵심인 3일·5일 결과이며, 전체 수치와 다른 보유기간은 CSV에 보존됩니다.", ""])
    quantile_summary = []
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in results["quantiles"]:
        if factor_is_available(records, row["factor"]) and row["target"] in ("excess_3d_pct", "excess_5d_pct"):
            grouped[(row["scope"], row["factor"], row["target"])].append(row)
    tolerance = float(parameters["FACTOR_MONOTONIC_TOLERANCE_PCT"])
    for (scope, factor, target), qrows in grouped.items():
        qrows.sort(key=lambda row: row["quantile"])
        quantile_summary.append({
            "scope": scope, "factor": FACTOR_LABELS[factor], "target": target,
            **{f"q{row['quantile']}": row["mean_return_pct"] for row in qrows},
            "monotonic": "Y" if is_monotonic(qrows, tolerance) else "N",
            "warning": "표본 제한 주의" if scope != "all" and regime_counts.get(scope, 0) < int(parameters["FACTOR_REGIME_MIN_DAYS"]) else "",
        })
    lines.extend(_table(quantile_summary, [
        ("scope", "구간"), ("factor", "요인"), ("target", "목표"), ("q1", "Q1"), ("q2", "Q2"),
        ("q3", "Q3"), ("q4", "Q4"), ("q5", "Q5"), ("monotonic", "단조성"), ("warning", "주의"),
    ]))
    lines.extend(["", "## 요인 간 중복 정보", "",
                  "절댓값이 큰 상관은 두 요인이 비슷한 정보를 반복할 가능성을 뜻합니다. 상수인 외부 요인은 계산할 수 없어 N/A입니다.", ""])
    matrix_rows = []
    for row in results["matrix"]:
        matrix_rows.append({"factor": FACTOR_LABELS[row["factor"]], **{FACTOR_LABELS[key]: value for key, value in row.items() if key != "factor"}})
    lines.extend(_table(matrix_rows, [("factor", "요인"), *[(FACTOR_LABELS[factor], FACTOR_LABELS[factor]) for factor in FACTORS]]))
    quality_counts: dict[str, int] = defaultdict(int)
    for row in quality_rows:
        quality_counts[str(row.get("reason") or "unknown")] += 1
    lines.extend(["", "## 범위·데이터·측정 정의", "",
                  f"- 가격 기간: `{manifest.get('start_date', parameters.get('BACKTEST_START_DATE'))}` ~ `{manifest.get('end_date', parameters.get('BACKTEST_END_DATE'))}`",
                  f"- 국면 거래일: 상승 {regime_counts.get('bull', 0)}일, 하락 {regime_counts.get('bear', 0)}일, 횡보 {regime_counts.get('sideways', 0)}일",
                  f"- 표본: 필수조건과 시장상승종목비율 조건 통과 후 미래 1거래일 시가가 존재하는 종목-일자 {len(records):,}건",
                  "- 미래수익률: 다음 거래일 시가 진입 후 1·3·5·10·20 거래일 종가 기준, 거래비용과 슬리피지 차감",
                  "- 초과수익률: 같은 진입일 KOSPI 시가부터 같은 보유기간 종가까지의 수익률을 차감",
                  f"- 품질 제외 기록: {len(quality_rows):,}건 ({', '.join(f'{key} {value}건' for key, value in sorted(quality_counts.items())) or '없음'})", "",
                  "## 검증 방법은 동일 날짜의 종목 군집을 고려했다", "",
                  "각 날짜마다 요인 순위와 미래수익률 순위의 스피어만 상관(IC)을 계산하고, 날짜별 IC 평균이 0과 다른지 점근 정규 검정했습니다. 유의수준은 설정값이며, 유의한 양의 3일·5일 IC와 두 기간의 분위 단조성을 모두 만족할 때만 ‘유의미한 예측력 있음’으로 분류합니다. 국면별 부호가 바뀌거나 일부 조건만 만족하면 ‘약하거나 불안정’으로 분류합니다.", "",
                  "## 한계와 불확실성", "",
                  f"- 하락장 {regime_counts.get('bear', 0)}거래일은 `{parameters['FACTOR_REGIME_MIN_DAYS']}`일 주의 기준보다 작아 표본 제한 주의 대상입니다.",
                  "- 현재 watchlist를 과거 전체에 적용하므로 생존편향이 있습니다.",
                  "- 일별 IC 검정은 날짜 내 군집 문제를 줄이지만 시계열 자기상관을 완전히 교정한 Newey-West 검정은 아닙니다.",
                  "- 외부요인은 저장된 실제 공개시각과 보수적 가용시각 규칙을 사용했으며, 낮은 커버리지 구간의 결론은 불확실합니다.",
                  "- 이 분석은 연관성과 예측 신호를 측정하며 인과관계를 입증하지 않습니다. 여러 요인·기간을 동시에 검정하므로 다중검정에 따른 우연한 유의성 가능성도 남습니다.", "",
                  "## 다음 단계 제안", "",
                  "- ‘유의미’ 판정 요인은 완전 미사용 기간에서 재검증한 뒤 유지 여부를 결정합니다.",
                  "- ‘약하거나 불안정’ 요인은 국면별 별도 사용과 축소안을 함께 백테스트합니다.",
                  "- ‘노이즈’ 판정 요인은 즉시 자동 제거하지 말고, 제거 전후 포트폴리오 백테스트로 확인합니다.",
                  "- 뉴스·공시·재무는 사건 발생시각·공개시각·당시 원문/수치를 저장하는 point-in-time 스냅샷부터 구축합니다.", "",
                  "## 추가로 답해야 할 질문", "",
                  "- 현재 watchlist가 아닌 당시 구성종목을 사용해도 순위가 유지되는가?",
                  "- 거래일 IC에 Newey-West 또는 블록 부트스트랩을 적용해도 유의성이 남는가?",
                  "- 여러 보유기간과 요인에 대한 FDR 보정 후에도 유효한 요인이 있는가?", "",
                  "분석 파라미터와 전체 원시 결과는 같은 디렉터리의 `factor_parameters.json` 및 CSV 파일에 저장했습니다."])
    return "\n".join(lines) + "\n"


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR, names: dict[str, str] | None = None) -> dict:
    load_env()
    engine = BacktestEngine(data_dir, report_dir, names=names)
    if not engine.rows.get(BENCHMARK) or not engine.regimes:
        raise RuntimeError("Backtest data is missing. Run: python -m stock_alarm.backtest_data")
    alpha = float(os.environ.get("FACTOR_SIGNIFICANCE_LEVEL", "0.05"))
    quantiles = int(os.environ.get("FACTOR_QUANTILES", "5"))
    minimum_cross_section = int(os.environ.get("FACTOR_MIN_CROSS_SECTION", "5"))
    limited_days = int(os.environ.get("FACTOR_REGIME_MIN_DAYS", "250"))
    monotonic_tolerance = float(os.environ.get("FACTOR_MONOTONIC_TOLERANCE_PCT", "0.05"))
    records = extract_factor_samples(engine)
    regime_day_counts = {regime: sum(value == regime for value in engine.regimes.values()) for regime in ("bull", "bear", "sideways")}
    results = analyze(records, alpha, quantiles, minimum_cross_section, monotonic_tolerance, regime_day_counts, limited_days)
    parameters = {
        "BACKTEST_START_DATE": os.environ.get("BACKTEST_START_DATE", "2022-01-01"),
        "BACKTEST_END_DATE": os.environ.get("BACKTEST_END_DATE", ""),
        "EXECUTION_COST_BPS": os.environ.get("EXECUTION_COST_BPS", "30"),
        "BACKTEST_SLIPPAGE_BPS": os.environ.get("BACKTEST_SLIPPAGE_BPS", "10"),
        "MIN_TRADING_VALUE": str(engine.min_trading_value), "VOLUME_MULTIPLIER": str(engine.volume_multiplier),
        "MIN_MARKET_UP_RATIO": str(engine.market_ratio), "FACTOR_SIGNIFICANCE_LEVEL": str(alpha),
        "FACTOR_QUANTILES": str(quantiles), "FACTOR_MIN_CROSS_SECTION": str(minimum_cross_section),
        "FACTOR_REGIME_MIN_DAYS": str(limited_days), "FACTOR_MONOTONIC_TOLERANCE_PCT": str(monotonic_tolerance),
        "horizons": list(HORIZONS), "regime_day_counts": regime_day_counts,
        "factor_sources": {factor: "historical_live_scoring_path" if factor in TECHNICAL_FACTORS else ("point_in_time_store" if factor_is_available(records, factor) else "unavailable_point_in_time_snapshot") for factor in FACTORS},
        "verdict_basis": "mean daily cross-sectional Spearman IC for 3d/5d excess returns",
        "visual_omission_reason": "Exact audit tables are more decision-useful than charts for seven factors, four scopes, and two primary horizons.",
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(report_dir / "factor_samples.csv", records)
    _write_csv(report_dir / "factor_correlations.csv", results["correlations"])
    _write_csv(report_dir / "factor_quantiles.csv", results["quantiles"])
    _write_csv(report_dir / "factor_correlation_matrix.csv", results["matrix"])
    _write_csv(report_dir / "factor_verdicts.csv", results["verdicts"])
    (report_dir / "factor_parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path, quality_path = report_dir / "data_manifest.json", report_dir / "data_quality.csv"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if quality_path.exists():
        with quality_path.open(newline="", encoding="utf-8-sig") as file:
            quality_rows = list(csv.DictReader(file))
    else:
        quality_rows = []
    report_path = report_dir / "FACTOR_REPORT.md"
    report_path.write_text(build_report(records, results, parameters, manifest, quality_rows), encoding="utf-8")
    result = {"samples": len(records), "report": str(report_path), "verdicts": {row["factor"]: row["verdict"] for row in results["verdicts"]}}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
