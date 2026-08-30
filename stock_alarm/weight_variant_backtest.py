from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from .app import load_env
from .backtest_data import DATA_DIR, REPORT_DIR
from .strategy_learning import FACTORS, return_distribution_p_value
from .validation_backtest import BacktestEngine, Trade, summarize_by_regime


CONFIG_PATH = Path("config/backtest_weight_variants.json")
EXTERNAL_FACTORS = ("news_score", "disclosure_score", "financial_score")
TECHNICAL_FACTORS = ("volume_score", "trading_value_score", "trend_score", "relative_strength_score")
FACTOR_LABELS = {
    "volume_score": "거래량", "trading_value_score": "거래대금", "trend_score": "추세",
    "relative_strength_score": "상대강도", "news_score": "뉴스", "disclosure_score": "공시",
    "financial_score": "재무",
}


def effective_points(base_points: dict[str, float], weights: dict[str, float]) -> dict[str, float]:
    return {factor: round(float(base_points[factor]) * float(weights[factor]), 6) for factor in FACTORS}


def validate_variant_config(config: dict) -> dict:
    variants = config.get("variants") or {}
    if "variant_1" not in variants or len(variants) < 2:
        raise ValueError("variant_1 baseline and at least one proposal are required")
    base_points = config.get("base_factor_points") or {}
    if set(base_points) != set(FACTORS):
        raise ValueError("base_factor_points must define all seven factors")
    baseline_weights = variants["variant_1"].get("weights") or {}
    for name, variant in variants.items():
        weights = variant.get("weights") or {}
        if set(weights) != set(FACTORS):
            raise ValueError(f"{name} must define all seven factor weights")
        if any(float(value) < 0 for value in weights.values()):
            raise ValueError(f"{name} contains a negative factor multiplier")
        for factor in EXTERNAL_FACTORS:
            if float(weights[factor]) != float(baseline_weights[factor]):
                raise ValueError(f"{name} changes protected external factor {factor}")
    return config


def load_variant_config(path: Path = CONFIG_PATH) -> dict:
    return validate_variant_config(json.loads(path.read_text(encoding="utf-8")))


def compare_distributions(baseline: list[Trade], proposed: list[Trade], alpha: float) -> dict:
    baseline_values = [trade.return_pct for trade in baseline]
    proposed_values = [trade.return_pct for trade in proposed]
    p_value = return_distribution_p_value(proposed_values, baseline_values)
    baseline_avg = mean(baseline_values) if baseline_values else 0.0
    proposed_avg = mean(proposed_values) if proposed_values else 0.0
    return {
        "baseline_samples": len(baseline_values), "variant_samples": len(proposed_values),
        "baseline_avg_return_pct": round(baseline_avg, 4), "variant_avg_return_pct": round(proposed_avg, 4),
        "avg_return_delta_pp": round(proposed_avg - baseline_avg, 4), "p_value": p_value,
        "return_improved": proposed_avg > baseline_avg,
        "statistically_significant": p_value < alpha,
        "significant_return_improvement": proposed_avg > baseline_avg and p_value < alpha,
    }


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        if not columns:
            return
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


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


def build_report(config: dict, performance: list[dict], comparisons: list[dict], parameters: dict) -> str:
    base_points = config["base_factor_points"]
    variant_rows = []
    for name, variant in config["variants"].items():
        points = effective_points(base_points, variant["weights"])
        variant_rows.append({
            "variant": name, "label": variant["label"],
            "volume": points["volume_score"], "trading_value": points["trading_value_score"],
            "trend": points["trend_score"], "relative_strength": f"±{points['relative_strength_score']:.4f}",
            "news": variant["weights"]["news_score"], "disclosure": variant["weights"]["disclosure_score"],
            "financial": variant["weights"]["financial_score"], "rationale": variant["rationale"],
        })
    overall_comparisons = [row for row in comparisons if row["regime"] == "all" and row["variant"] != "variant_1"]
    winners = [row for row in overall_comparisons if row["promotion_candidate"]]
    if winners:
        conclusion = "통계·수익·낙폭·초과수익 조건을 모두 만족한 후보: " + ", ".join(row["variant"] for row in winners)
    else:
        conclusion = "현재 데이터로는 어떤 가중치 조정도 근거가 부족하다. 실제 운영 가중치는 유지한다."
    lines = [
        "# stockAlarm 요인 가중치 포트폴리오 백테스트", "", "## 기술 요약", "",
        f"**{conclusion}**", "",
        "네 변형안을 동일한 2022년 이후 격리 OHLCV, 다음 거래일 시가 진입, 거래비용·슬리피지, 기존 매도 및 분할익절 로직으로 비교했습니다. 뉴스·공시·재무 배수는 모든 안에서 baseline과 동일한 1.0이며 실제 운영 설정과 DB는 변경하지 않았습니다.",
        f"통계 검정은 전략 승격 로직과 같은 단측 Mann–Whitney U 검정을 사용했습니다. `promotion_candidate`는 평균수익률·누적수익률·평균 초과수익률 개선, MDD 2%p 이내, p-value < {parameters['alpha']}를 모두 요구합니다.", "",
        "## 실험 가중치는 네 가지 가설을 분리한다", "",
        "표의 기술요인 값은 배수 적용 후 유효 점수 상한입니다. 상대강도는 양·음 방향으로 작동하므로 ±로 표시합니다. 외부요인은 원점수 배수이며 모든 안에서 고정했습니다.", "",
    ]
    lines.extend(_table(variant_rows, [
        ("variant", "안"), ("volume", "거래량"), ("trading_value", "거래대금"), ("trend", "추세"),
        ("relative_strength", "상대강도"), ("news", "뉴스 배수"), ("disclosure", "공시 배수"),
        ("financial", "재무 배수"), ("rationale", "설계 근거"),
    ]))
    lines.extend(["", "## 전체 기간 성과가 baseline보다 개선됐는가", "",
                  "누적수익률과 MDD는 기존 백테스트와 동일하게 신호당 고정 자본배분을 순차 반영한 포트폴리오 프록시입니다. 실제 일별 현금·평가금액 기반 NAV와는 다릅니다.", ""])
    lines.extend(_table([row for row in performance if row["regime"] == "all"], [
        ("variant", "안"), ("samples", "거래 수"), ("total_return_pct", "누적수익률%"),
        ("avg_return_pct", "평균수익률%"), ("win_rate_pct", "승률%"), ("mdd_pct", "MDD%"),
        ("avg_excess_return_pct", "평균 초과수익률%"), ("partial_exit_count", "분할익절 수"),
    ]))
    lines.extend(["", "## 국면별로 개선 방향이 일관적인가", "",
                  "국면별 성과는 신호일의 KOSPI 국면 라벨을 기준으로 나눴습니다. 하락장 174거래일은 표본 제한 주의 대상입니다.", ""])
    lines.extend(_table([row for row in performance if row["regime"] != "all"], [
        ("variant", "안"), ("regime", "국면"), ("samples", "거래 수"), ("total_return_pct", "누적수익률%"),
        ("avg_return_pct", "평균수익률%"), ("win_rate_pct", "승률%"), ("mdd_pct", "MDD%"),
        ("avg_excess_return_pct", "평균 초과수익률%"), ("sample_warning", "주의"),
    ]))
    lines.extend(["", "## baseline 대비 차이가 통계적으로 유의한가", "",
                  "p-value는 proposed 수익률 분포가 baseline보다 높다는 단측 검정입니다. 거래 선택이 달라 표본 수가 다를 수 있으며, 같은 시장일 신호 간 의존성은 이 검정에서 완전히 제거되지 않습니다.", ""])
    lines.extend(_table([row for row in comparisons if row["variant"] != "variant_1"], [
        ("variant", "안"), ("regime", "국면"), ("baseline_samples", "기존 표본"), ("variant_samples", "변형 표본"),
        ("avg_return_delta_pp", "평균수익률 차이pp"), ("total_return_delta_pp", "누적수익률 차이pp"),
        ("mdd_delta_pp", "MDD 차이pp"), ("excess_return_delta_pp", "초과수익률 차이pp"),
        ("p_value", "p-value"), ("promotion_candidate", "종합 통과"),
    ]))
    lines.extend(["", "## 범위·정의·재현 조건", "",
                  f"- 분석 기간: `{parameters['BACKTEST_START_DATE']}` ~ `{parameters['BACKTEST_END_DATE']}`",
                  "- 후보 선정: 라이브 추천 평가 함수, 시장 필터, 점수 임계값, TOP_N을 그대로 재사용",
                  "- 진입: 추천 다음 거래일 시가",
                  "- 청산: 기존 동적 손절·20일선 이탈·수익반납·기간청산·1차/2차 분할익절",
                  f"- 비용: 체결비용 {parameters['EXECUTION_COST_BPS']}bp + 슬리피지 {parameters['BACKTEST_SLIPPAGE_BPS']}bp",
                  f"- 통계 유의수준: {parameters['alpha']}",
                  "- 출력 경로만 `reports/backtest`를 사용하며 라이브 DB와 가상계좌는 읽거나 수정하지 않음", "",
                  "## 한계와 불확실성", "",
                  "- 현재 watchlist를 과거 전 기간에 적용하는 생존편향이 있습니다.",
                  "- 종목별 시그널 수익률에 고정 자본배분을 적용한 포트폴리오 프록시라 실제 동시 체결·현금잔고·일별 NAV와 차이가 있습니다.",
                  "- Mann–Whitney 검정은 거래 수익률을 독립 표본으로 가정하므로 같은 날짜·시장국면의 공통 충격을 과소평가할 수 있습니다.",
                  "- 여러 variant와 국면을 동시에 비교하므로 다중검정에 의한 우연한 유의성 가능성이 있습니다.",
                  "- 뉴스·공시·재무 배수는 그대로지만 과거 시점 스냅샷 부재로 이 백테스트에서는 값이 0입니다. 외부요인의 예측력 결론으로 해석하면 안 됩니다.", "",
                  "## 종합 결론과 제안", "", f"**{conclusion}**", "",
                  "- 이 결과만으로 실제 운영 가중치를 자동 변경하지 않습니다.",
                  "- 종합 통과 후보가 있더라도 완전 미사용 기간과 일별 NAV 기반 포트폴리오 시뮬레이션에서 재검증한 뒤 사람이 판단합니다.",
                  "- 종합 통과 후보가 없으면 현재 가중치를 유지하고, point-in-time 외부요인 데이터와 더 긴 국면 표본을 축적합니다.", "",
                  "## 추가 검증 질문", "",
                  "- 일별 현금·보유비중·동시 포지션 한도를 완전히 재현해도 결과가 유지되는가?",
                  "- 거래일 단위 블록 부트스트랩이나 다중검정 보정 후에도 유의성이 남는가?",
                  "- 현재 watchlist가 아닌 당시 구성종목을 사용해도 성과 차이가 유지되는가?", "",
                  "정확한 가중치 배수, 환경값, 전체 거래와 비교 결과는 같은 디렉터리의 JSON·CSV 파일에 저장했습니다."])
    return "\n".join(lines) + "\n"


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR, config_path: Path = CONFIG_PATH) -> dict:
    load_env()
    config = load_variant_config(config_path)
    alpha = float(os.environ.get("WEIGHT_VARIANT_SIGNIFICANCE_LEVEL", os.environ.get("LEARNING_SIGNIFICANCE_LEVEL", "0.05")))
    trades_by_variant: dict[str, list[Trade]] = {}
    performance = []
    for name, variant in config["variants"].items():
        engine = BacktestEngine(data_dir, report_dir, variant["weights"])
        trades, _learning_rows = engine.run(partial_profit=True)
        trades_by_variant[name] = trades
        rows = summarize_by_regime(trades, name)
        for row in rows:
            row["sample_warning"] = "표본 제한 주의" if row["regime"] == "bear" else ""
        performance.extend(rows)
    metric_lookup = {(row["variant"], row["regime"]): row for row in performance}
    comparisons = []
    baseline = trades_by_variant["variant_1"]
    for name, trades in trades_by_variant.items():
        for regime in ("all", "bull", "bear", "sideways"):
            baseline_scope = baseline if regime == "all" else [trade for trade in baseline if trade.regime == regime]
            proposed_scope = trades if regime == "all" else [trade for trade in trades if trade.regime == regime]
            comparison = compare_distributions(baseline_scope, proposed_scope, alpha)
            base_metric, proposed_metric = metric_lookup[("variant_1", regime)], metric_lookup[(name, regime)]
            comparison.update({
                "variant": name, "regime": regime,
                "total_return_delta_pp": round(float(proposed_metric["total_return_pct"]) - float(base_metric["total_return_pct"]), 4),
                "mdd_delta_pp": round(float(proposed_metric["mdd_pct"]) - float(base_metric["mdd_pct"]), 4),
                "excess_return_delta_pp": round(float(proposed_metric["avg_excess_return_pct"]) - float(base_metric["avg_excess_return_pct"]), 4),
            })
            comparison["promotion_candidate"] = bool(
                comparison["significant_return_improvement"]
                and comparison["total_return_delta_pp"] > 0
                and comparison["mdd_delta_pp"] >= -2
                and comparison["excess_return_delta_pp"] > 0
            )
            comparisons.append(comparison)
    parameters = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "BACKTEST_START_DATE": os.environ.get("BACKTEST_START_DATE", "2022-01-01"),
        "BACKTEST_END_DATE": os.environ.get("BACKTEST_END_DATE") or datetime.now().date().isoformat(),
        "EXECUTION_COST_BPS": os.environ.get("EXECUTION_COST_BPS", "30"),
        "BACKTEST_SLIPPAGE_BPS": os.environ.get("BACKTEST_SLIPPAGE_BPS", "10"),
        "BACKTEST_TRADE_ALLOCATION_PCT": os.environ.get("BACKTEST_TRADE_ALLOCATION_PCT", "10"),
        "alpha": alpha, "variant_config": str(config_path),
        "live_config_changed": False, "database_access": "none",
        "visual_omission_reason": "Exact four-variant by four-regime comparison tables are clearer and more auditable than a chart.",
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    trade_rows = [{"variant": name, **asdict(trade)} for name, trades in trades_by_variant.items() for trade in trades]
    _write_csv(report_dir / "weight_variant_trades.csv", trade_rows)
    _write_csv(report_dir / "weight_variant_performance.csv", performance)
    _write_csv(report_dir / "weight_variant_comparison.csv", comparisons)
    (report_dir / "weight_variant_parameters.json").write_text(
        json.dumps({"parameters": parameters, "config": config}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = report_dir / "WEIGHT_VARIANT_REPORT.md"
    report_path.write_text(build_report(config, performance, comparisons, parameters), encoding="utf-8")
    overall = [row for row in comparisons if row["regime"] == "all" and row["variant"] != "variant_1"]
    result = {
        "report": str(report_path), "variants": len(trades_by_variant),
        "promotion_candidates": [row["variant"] for row in overall if row["promotion_candidate"]],
        "live_config_changed": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
