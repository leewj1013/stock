from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from .backtest_data import BENCHMARK, DATA_DIR, REPORT_DIR
from .benchmark_comparison import (
    PortfolioResult,
    PortfolioSimulator,
    compound_return,
    daily_return_map,
    equity_metrics,
)
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine


OUTPUT_DIR = REPORT_DIR / "gap_decomposition"
REPORT_PATH = REPORT_DIR / "GAP_DECOMPOSITION_REPORT.md"
CHART_PATH = REPORT_DIR / "GAP_DECOMPOSITION_WATERFALL.png"
HORIZONS = (20, 40, 60)


def weighted_cash_missed_returns(daily_state: list[dict], market_returns: dict[str, float]) -> list[dict]:
    """Measure foregone market return using prior-close cash exposure."""
    rows = []
    previous_cash_weight = 1.0
    previous_defensive_mode = False
    previous_idle_reason = "initial_cash"
    for state in daily_state:
        day = str(state["date"])
        market_return = float(market_returns.get(day, 0.0))
        missed = previous_cash_weight * market_return
        rows.append({
            "date": day, "cash_weight_start": previous_cash_weight,
            "market_return": market_return, "missed_return": missed,
            # Today's market return is earned by the allocation established at
            # the prior close. Attribute its cash drag to that prior state too.
            "defensive_mode": previous_defensive_mode,
            "idle_reason": previous_idle_reason,
        })
        previous_cash_weight = float(state.get("cash_weight") or 0.0)
        previous_defensive_mode = bool(state.get("defensive_mode", False))
        previous_idle_reason = str(state.get("idle_reason", ""))
    return rows


def aggregate_transaction_costs(events: list[dict], initial_cash: float, years: float,
                                average_equity: float | None = None) -> dict:
    sells = [row for row in events if str(row.get("event", "")).startswith("sell")]
    total_cost = sum(float(row.get("transaction_cost") or 0) for row in sells)
    traded_notional = sum(float(row.get("gross_notional") or 0) for row in events)
    denominator = average_equity if average_equity and average_equity > 0 else initial_cash
    return {
        "sell_event_count": len(sells), "total_cost": total_cost,
        "cost_pct_initial_capital": total_cost / initial_cash * 100 if initial_cash else 0.0,
        "traded_notional": traded_notional,
        "annual_turnover": traded_notional / denominator / years if denominator and years > 0 else 0.0,
    }


def _future_close(engine: BacktestEngine, ticker: str, day: str, horizon: int) -> float | None:
    item = engine.by_date.get(ticker, {}).get(day)
    rows = engine.rows.get(ticker, [])
    if not item or item[0] + horizon >= len(rows):
        return None
    return float(rows[item[0] + horizon][4])


def post_sale_tracking(events: list[dict], engine: BacktestEngine, horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    output = []
    for event in events:
        if not str(event.get("event", "")).startswith("sell") or event.get("reason") == "end_of_test":
            continue
        sale_price = float(event.get("price") or 0)
        if sale_price <= 0:
            continue
        row = dict(event)
        for horizon in horizons:
            future = _future_close(engine, str(event["ticker"]), str(event["date"]), horizon)
            market_future = _future_close(engine, BENCHMARK, str(event["date"]), horizon)
            stock_return = (future / sale_price - 1) if future is not None else None
            benchmark_return = (market_future / float(engine.by_date[BENCHMARK][str(event["date"])][1][4]) - 1) if market_future is not None and str(event["date"]) in engine.by_date.get(BENCHMARK, {}) else None
            row[f"post_{horizon}d_return_pct"] = stock_return * 100 if stock_return is not None else None
            row[f"post_{horizon}d_benchmark_pct"] = benchmark_return * 100 if benchmark_return is not None else None
            row[f"post_{horizon}d_rebounded"] = stock_return is not None and stock_return > 0
            equity = float(event.get("portfolio_equity_before") or 0)
            weight = float(event.get("gross_notional") or 0) / equity if equity > 0 else 0.0
            row[f"opportunity_contribution_{horizon}d_pp"] = weight * stock_return * 100 if stock_return is not None else None
        output.append(row)
    return output


def selection_effect(events: list[dict], engine: BacktestEngine) -> list[dict]:
    """Compare each sold slice with KOSPI over the identical entry/sale window."""
    rows = []
    for event in events:
        if not str(event.get("event", "")).startswith("sell"):
            continue
        entry_price = float(event.get("entry_price") or 0)
        sale_price = float(event.get("price") or 0)
        entry_day, sale_day = str(event.get("entry_date") or ""), str(event.get("date") or "")
        benchmark_entry = engine.by_date.get(BENCHMARK, {}).get(entry_day)
        benchmark_exit = engine.by_date.get(BENCHMARK, {}).get(sale_day)
        if entry_price <= 0 or not benchmark_entry or not benchmark_exit:
            continue
        stock_return = sale_price / entry_price - 1
        benchmark_open = float(benchmark_entry[1][1])
        benchmark_return = float(benchmark_exit[1][4]) / benchmark_open - 1 if benchmark_open else 0.0
        excess = stock_return - benchmark_return
        entry_equity = float(event.get("entry_equity") or 0)
        basis_slice = entry_price * float(event.get("shares") or 0)
        contribution = basis_slice / entry_equity * excess * 100 if entry_equity > 0 else 0.0
        rows.append({
            "date": sale_day, "signal_date": event.get("signal_date"), "ticker": event.get("ticker"),
            "event": event.get("event"), "reason": event.get("reason"),
            "stock_holding_return_pct": stock_return * 100,
            "benchmark_same_window_pct": benchmark_return * 100,
            "excess_return_pct": excess * 100, "portfolio_contribution_pp": contribution,
        })
    return rows


def overlay_cash_on_market(base: PortfolioResult, missed_rows: list[dict]) -> float:
    base_returns = daily_return_map(base)
    adjusted = [base_returns.get(row["date"], 0.0) + float(row["missed_return"]) for row in missed_rows]
    return compound_return(adjusted) * 100


def _reason_group(reason: str, stage: str) -> str:
    text = f"{reason} {stage}".lower()
    if "take_profit" in text or "익절" in text:
        return "분할익절"
    if "ma20" in text or "20일" in text:
        return "20일선 이탈"
    if "stop" in text or "손절" in text:
        return "손절"
    if "time" in text or "기간" in text:
        return "기간청산"
    if "보유 후" in text or "기대수익 미달" in text:
        return "기간청산"
    if "giveback" in text or "수익반납" in text:
        return "수익반납"
    return "기타"


def _scope_rows(rows: list[dict], regimes: dict[str, str], scope: str, date_key: str = "date") -> list[dict]:
    return rows if scope == "all" else [row for row in rows if regimes.get(str(row.get(date_key, ""))) == scope]


def _safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _percent(value) -> str:
    return "N/A" if value is None else f"{float(value):.2f}%"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    def fmt(value):
        if value is None or value == "":
            return "N/A"
        if isinstance(value, bool):
            return "Y" if value else "N"
        return f"{value:.4f}" if isinstance(value, float) else str(value)
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def create_waterfall_chart(rows: list[dict], path: Path) -> None:
    """Render the exact additive bridge; the five-factor overlay is not additive."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [row["label"] for row in rows]
    values = [float(row["value_pp"]) for row in rows]
    starts, heights, colors = [], [], []
    cumulative = values[0]
    starts.append(0.0)
    heights.append(cumulative)
    colors.append("#4B5563")
    for value in values[1:-1]:
        starts.append(cumulative if value >= 0 else cumulative + value)
        heights.append(abs(value))
        colors.append("#D97706" if value >= 0 else "#2563EB")
        cumulative += value
    starts.append(0.0)
    heights.append(values[-1])
    colors.append("#4B5563")
    figure, axis = plt.subplots(figsize=(10.5, 5.8))
    bars = axis.bar(range(len(rows)), heights, bottom=starts, color=colors, edgecolor="#1F2937", linewidth=0.8)
    for index, (bar, row) in enumerate(zip(bars, rows)):
        if index in (0, len(rows) - 1):
            label = f"{row['value_pp']:.2f}%"
            y = starts[index] + heights[index]
        else:
            label = f"{row['value_pp']:+.2f}%p"
            y = starts[index] + heights[index]
        axis.text(bar.get_x() + bar.get_width() / 2, y + max(values[-1] * 0.015, 1), label, ha="center", va="bottom", fontsize=10)
    axis.set_xticks(range(len(labels)), labels)
    axis.set_ylabel("누적 수익률 / 격차 기여 (%·%p)")
    axis.set_title("stockAlarm → KOSPI 수익률 격차의 정확 합산 브리지")
    axis.grid(axis="y", color="#D1D5DB", linewidth=0.6, alpha=0.7)
    axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def build_report(parameters: dict, factor_rows: list[dict], regime_rows: list[dict], bridge_rows: list[dict],
                 cash_summary: dict, cost_summary: dict, exit_summary: list[dict], selection_summary: dict,
                 sizing_summary: dict) -> str:
    ranked = sorted([row for row in factor_rows if row["factor"] != "기타/미분해"], key=lambda row: float(row["gap_contribution_pp"]), reverse=True)
    top_three = [row for row in ranked if float(row["gap_contribution_pp"]) > 0][:3]
    top_text = ", ".join(f"{row['factor']}({_percent(row['gap_contribution_pp'])}p)" for row in top_three) or "양의 손실 기여 요인 없음"
    lines = [
        "# stockAlarm–KOSPI 수익률 격차 원인 분해", "", "## 기술 요약", "",
        f"stockAlarm `{parameters['stock_return_pct']:.2f}%`와 KOSPI `{parameters['kospi_return_pct']:.2f}%`의 격차는 `{parameters['total_gap_pp']:.2f}%p`입니다.",
        f"진단상 가장 큰 손실 원인 Top 3는 **{top_text}**입니다.",
        "다섯 요인은 정확 합산 브리지 안에서 순차 배정했지만, 조기매도·종목선정·비중 반사실적의 몫은 적용 순서에 따라 달라질 수 있어 인과효과로 단정할 수 없습니다.", "",
        "## 정확 합산 브리지는 현금·비용 뒤 남는 투자경로 격차가 대부분이다", "",
        "아래 브리지는 순서대로 적용해 KOSPI 종점과 정확히 일치합니다. 거래비용을 되돌리고, 전일 현금을 KOSPI에 투자했다고 가정한 뒤 남는 차이를 종목선정·매도시점·비중경로가 결합된 ‘투자경로 격차’로 둡니다.", "",
        f"![정확 합산 워터폴]({CHART_PATH.name})", "",
    ]
    lines.extend(_table(bridge_rows, [("rank", "순서"), ("label", "항목"), ("value_pp", "수익률/%p"), ("cumulative_pct", "누적 수익률%")]))
    lines.extend(["", "## 5요인 순차 배정은 전체 격차와 일치하지만 순서 의존적이다", "",
                  "양수는 KOSPI와의 격차를 키운 손실 요인, 음수는 stockAlarm에 도움이 된 요인입니다. 비용과 현금은 직접 반사실적으로 먼저 확정하고, 남은 투자경로 격차에서 종목선정·비중 효과를 뺀 잔여를 조기매도/타이밍에 배정했습니다.", ""])
    lines.extend(_table(factor_rows, [
        ("rank", "순위"), ("factor", "요인"), ("gap_contribution_pp", "격차 기여 %p"),
        ("method", "산정 방법"), ("confidence", "해석 신뢰도"),
    ]))
    lines.extend(["", "## 현금 대기는 평균 노출과 놓친 시장 상승분으로 측정했다", "",
                  f"평균 현금 비중은 **{cash_summary['average_cash_weight_pct']:.2f}%**, 현금 50% 이상인 날은 **{cash_summary['cash_over_50_days']}일**입니다. 전일 현금 비중에 당일 KOSPI 수익률을 곱한 단순 누계는 **{cash_summary['arithmetic_missed_pp']:.2f}%p**, 거래비용 제거 후 일별 현금을 KOSPI로 채운 순차 반사실적 효과는 **{cash_summary['exact_overlay_effect_pp']:.2f}%p**입니다.",
                  f"공통 라이브 위험중단 평가기를 적용한 방어모드 일수는 **{cash_summary['defensive_days']}일**입니다. 무비용 경로에서 방어모드가 유지한 현금의 순차 반사실적 효과는 **{cash_summary['defensive_overlay_effect_pp']:.2f}%p**, 일반 유휴현금 효과는 **{cash_summary['general_overlay_effect_pp']:.2f}%p**입니다. 두 값의 합이 전체 현금 대기 효과와 일치합니다.", "",
                  "## 조기매도 뒤 반등은 20·40·60거래일로 추적했다", "",
                  "매도 후 수익률은 실행 가능 포트폴리오가 아니라 ‘그 물량을 계속 들고 있었다면’이라는 진단값입니다. 같은 자금을 다른 종목에 재투자했을 가능성을 무시하므로 기여도 합산 시 중복될 수 있습니다.", ""])
    lines.extend(_table(exit_summary, [
        ("reason_group", "매도유형"), ("events", "건수"), ("rebound_20d_pct", "20일 반등률%"),
        ("avg_post_20d_pct", "20일 평균%"), ("rebound_60d_pct", "60일 반등률%"),
        ("avg_post_60d_pct", "60일 평균%"), ("opportunity_60d_pp", "60일 원시 민감도 %p"),
    ]))
    lines.extend(["", "## 거래비용은 실제 이벤트 원장에서 집계했다", "",
                  f"매도 이벤트 **{cost_summary['sell_event_count']}건**에서 비용 **{cost_summary['total_cost']:,.0f}원**, 초기자본 대비 **{cost_summary['cost_pct_initial_capital']:.2f}%**가 발생했습니다. 연환산 회전율은 **{cost_summary['annual_turnover']:.2f}배**입니다. 비용을 0으로 재실행한 정확 성과 차이는 **{cost_summary['exact_return_effect_pp']:.2f}%p**입니다.", "",
                  "## 종목선정은 같은 보유창의 KOSPI와 비교했다", "",
                  f"실제 매도 물량 단위로 진입 시가부터 매도 종가까지 비교했을 때 평균 종목수익률은 **{selection_summary['avg_stock_return_pct']:.2f}%**, 동일 창 KOSPI는 **{selection_summary['avg_benchmark_return_pct']:.2f}%**, 평균 초과수익률은 **{selection_summary['avg_excess_return_pct']:.2f}%p**였습니다. KOSPI를 이긴 매도 물량 비율은 **{selection_summary['outperformance_rate_pct']:.2f}%**입니다.",
                  f"진입 당시 계좌비중으로 근사한 누적 순수 선정효과는 **{selection_summary['portfolio_contribution_pp']:.2f}%p**이며, 격차 기여 표에서는 부호를 뒤집어 선정이 좋으면 손실 기여가 음수가 되도록 표시했습니다. 단순 평균과 계좌비중 반영 합계는 표본 가중 방식이 달라 방향이나 크기가 달라질 수 있습니다.", "",
                  "## 비중 상한 제거 반사실적은 수익뿐 아니라 위험도 크게 바꾼다", "",
                  f"기준 고정 10% 슬롯은 `{sizing_summary['baseline_return_pct']:.2f}%`였고, 선택된 다음 시가 주문에 가용 현금을 모두 균등 배분하는 무상한 시나리오는 `{sizing_summary['unconstrained_return_pct']:.2f}%`였습니다. 차이는 **{sizing_summary['return_effect_pp']:.2f}%p**, MDD 변화는 `{sizing_summary['baseline_mdd_pct']:.2f}%`→`{sizing_summary['unconstrained_mdd_pct']:.2f}%`입니다.",
                  f"이 시나리오는 실제 체결 가능성과 리스크 관리 성공을 보장하지 않습니다. 기준 경로에는 라이브와 동일한 고상관 연결그룹 40% 신규진입 제한이 적용됐고, 이 제한으로 거절된 신규 신호는 **{cash_summary['correlation_rejected_signals']}건**입니다. 이미 보유한 물량은 강제매도하지 않으므로 가격변동 뒤 실제 보유비중이 40%를 넘을 수 있습니다.", "",
                  "## 상승장에서 현금과 투자경로의 차이가 핵심인지 국면별로 확인했다", "",
                  "국면별 표는 현금의 단순 일별 누계와 조기매도 60일 원시 민감도를 사용합니다. 중복은 기타/중복 열이 흡수하므로 전체 순차 배정표와 숫자가 직접 일치하지 않습니다.", ""])
    lines.extend(_table(regime_rows, [
        ("regime", "국면"), ("stock_return_pct", "stockAlarm%"), ("kospi_return_pct", "KOSPI%"),
        ("gap_pp", "격차%p"), ("cash_pp", "현금%p"), ("defensive_cash_pp", "방어 현금%p"),
        ("general_cash_pp", "일반 현금%p"), ("early_exit_pp", "조기매도%p"),
        ("cost_pp", "비용%p"), ("selection_pp", "선정%p"), ("sizing_pp", "비중%p"),
        ("residual_pp", "기타/중복%p"),
    ]))
    lines.extend(["", "## 범위·정의·방법", "",
                  f"- 평가기간: `{parameters['actual_start_date']}`~`{parameters['actual_end_date']}` ({parameters['trading_days']}거래일)",
                  "- 기준: 직전 벤치마크 비교와 같은 현재 활성 가중치, 다음 거래일 시가 진입, 10% 슬롯, 동일 손절·분할익절·비용",
                  "- 격차: KOSPI Buy & Hold 총수익률 − stockAlarm 총수익률",
                  "- 국면별 수익률: 해당 국면의 일별 수익률만 조건부 복리 결합; 독립된 연속 운용성과가 아님",
                  "- 원장: 일별 종가 기준 현금·보유비중, 주문 대기, 부분/전량 매도, 비용 이벤트", "",
                  "## 한계·불확실성·강건성", "",
                  "- 다섯 진단값은 상호작용 때문에 완전한 인과 분해가 아닙니다. 정확히 더해지는 3단계 브리지를 별도로 제공합니다.",
                  "- 조기매도 60일 기회비용은 재투자 대안을 무시하므로 실행 가능한 초과수익이 아닙니다.",
                  "- 비중 무상한 시나리오는 집중위험·유동성·상관관계를 무시하며 실제 실행 가능했다는 보장이 없습니다.",
                  "- 현재 watchlist의 과거 적용, 2022년 초 국면 워밍업, 과거 뉴스·공시·재무 시점자료 부재 한계가 그대로 있습니다.",
                  "- 위험중단은 일봉 엔진 특성상 전일 종가를 당일 기준자산, 직전 거래일 종가를 주간 시작자산으로 사용하는 일별 근사입니다. 라이브 장중 스냅샷과 관측 빈도는 다릅니다.",
                  "- 고상관 40%는 신규진입 배분 게이트이며 기존 보유분 강제축소 규칙은 아닙니다.", "",
                  "## 다음 판단을 위해 남는 질문", "",
                  "- 장중 자산 스냅샷을 복원하면 일봉 근사 대비 위험중단 진입·해제 시점이 얼마나 달라지는가?",
                  "- 당시 구성종목과 point-in-time 외부요인을 복원해도 종목선정 초과수익이 유지되는가?",
                  "- 조기매도 후 재투자된 대체 종목까지 연결한 자본경로 분석에서도 기회비용이 남는가?", "",
                  "## 재현 파라미터", "", "```json", json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR) -> dict:
    initial_cash = float(os.environ.get("BACKTEST_INITIAL_CASH", "100000000"))
    allocation_pct = float(os.environ.get("BACKTEST_TRADE_ALLOCATION_PCT", "10"))
    seed = int(os.environ.get("BENCHMARK_RANDOM_SEED", "20260828"))
    weights = active_weights()
    engine = BacktestEngine(data_dir, report_dir, score_weights=weights)
    simulator = PortfolioSimulator(engine, initial_cash, allocation_pct)
    candidates = simulator.build_candidate_cache()
    baseline = simulator.run("stock_alarm", candidates, seed)
    kospi = simulator.buy_and_hold("kospi_buy_hold", [BENCHMARK])
    unconstrained = simulator.run("stock_alarm", candidates, seed, unconstrained_sizing=True)
    no_cost_simulator = PortfolioSimulator(engine, initial_cash, allocation_pct)
    no_cost_simulator.cost_rate = 0.0
    no_cost = no_cost_simulator.run("stock_alarm", candidates, seed)
    regimes = engine.regimes
    baseline_metrics = {scope: equity_metrics(baseline.daily_equity, baseline.trades, regimes, scope) for scope in ("all", "bull", "bear", "sideways")}
    kospi_metrics = {scope: equity_metrics(kospi.daily_equity, kospi.trades, regimes, scope) for scope in ("all", "bull", "bear", "sideways")}
    unconstrained_metrics = {scope: equity_metrics(unconstrained.daily_equity, unconstrained.trades, regimes, scope) for scope in ("all", "bull", "bear", "sideways")}
    no_cost_metrics = {scope: equity_metrics(no_cost.daily_equity, no_cost.trades, regimes, scope) for scope in ("all", "bull", "bear", "sideways")}
    market_returns = daily_return_map(kospi)
    missed_rows = weighted_cash_missed_returns(baseline.daily_state, market_returns)
    no_cost_missed_rows = weighted_cash_missed_returns(no_cost.daily_state, market_returns)
    cash_overlay_return = overlay_cash_on_market(no_cost, no_cost_missed_rows)
    defensive_missed_rows = [
        {**row, "missed_return": row["missed_return"] if row["defensive_mode"] else 0.0}
        for row in no_cost_missed_rows
    ]
    defensive_overlay_return = overlay_cash_on_market(no_cost, defensive_missed_rows)
    post_sale_rows = post_sale_tracking(baseline.events, engine)
    selection_rows = selection_effect(baseline.events, engine)
    years = len(simulator.days) / 252
    average_equity = mean(float(row["equity"]) for row in baseline.daily_equity)
    cost_summary = aggregate_transaction_costs(baseline.events, initial_cash, years, average_equity)
    cost_summary["exact_return_effect_pp"] = no_cost_metrics["all"]["total_return_pct"] - baseline_metrics["all"]["total_return_pct"]
    cash_summary = {
        "average_cash_weight_pct": mean(float(row["cash_weight"]) for row in baseline.daily_state) * 100,
        "cash_over_50_days": sum(float(row["cash_weight"]) >= .5 for row in baseline.daily_state),
        "defensive_days": sum(bool(row["defensive_mode"]) for row in baseline.daily_state),
        "arithmetic_missed_pp": sum(float(row["missed_return"]) for row in missed_rows) * 100,
        "exact_overlay_effect_pp": cash_overlay_return - no_cost_metrics["all"]["total_return_pct"],
        "defensive_overlay_effect_pp": defensive_overlay_return - no_cost_metrics["all"]["total_return_pct"],
        "general_overlay_effect_pp": cash_overlay_return - defensive_overlay_return,
        "risk_halt_transitions": sum(row.get("risk_transition") == "halted" for row in baseline.daily_state),
        "risk_resume_transitions": sum(row.get("risk_transition") == "resumed" for row in baseline.daily_state),
        "correlation_rejected_signals": sum(int(row.get("correlation_rejected_count") or 0) for row in baseline.daily_state),
    }
    exit_summary = []
    grouped_exits = defaultdict(list)
    for row in post_sale_rows:
        grouped_exits[_reason_group(str(row.get("reason", "")), str(row.get("stage", "")))].append(row)
    for reason, rows in sorted(grouped_exits.items()):
        h20 = [float(row["post_20d_return_pct"]) for row in rows if row.get("post_20d_return_pct") is not None]
        h60 = [float(row["post_60d_return_pct"]) for row in rows if row.get("post_60d_return_pct") is not None]
        exit_summary.append({
            "reason_group": reason, "events": len(rows),
            "rebound_20d_pct": sum(value > 0 for value in h20) / len(h20) * 100 if h20 else None,
            "avg_post_20d_pct": _safe_mean(h20),
            "rebound_60d_pct": sum(value > 0 for value in h60) / len(h60) * 100 if h60 else None,
            "avg_post_60d_pct": _safe_mean(h60),
            "opportunity_60d_pp": sum(float(row.get("opportunity_contribution_60d_pp") or 0) for row in rows),
        })
    selection_summary = {
        "avg_stock_return_pct": mean(float(row["stock_holding_return_pct"]) for row in selection_rows),
        "avg_benchmark_return_pct": mean(float(row["benchmark_same_window_pct"]) for row in selection_rows),
        "avg_excess_return_pct": mean(float(row["excess_return_pct"]) for row in selection_rows),
        "outperformance_rate_pct": sum(float(row["excess_return_pct"]) > 0 for row in selection_rows) / len(selection_rows) * 100,
        "portfolio_contribution_pp": sum(float(row["portfolio_contribution_pp"]) for row in selection_rows),
    }
    sizing_summary = {
        "baseline_return_pct": baseline_metrics["all"]["total_return_pct"],
        "unconstrained_return_pct": unconstrained_metrics["all"]["total_return_pct"],
        "return_effect_pp": unconstrained_metrics["all"]["total_return_pct"] - baseline_metrics["all"]["total_return_pct"],
        "baseline_mdd_pct": baseline_metrics["all"]["mdd_pct"],
        "unconstrained_mdd_pct": unconstrained_metrics["all"]["mdd_pct"],
    }
    gap = kospi_metrics["all"]["total_return_pct"] - baseline_metrics["all"]["total_return_pct"]
    early_exit_pp = sum(float(row.get("opportunity_contribution_60d_pp") or 0) for row in post_sale_rows)
    active_path_gap = kospi_metrics["all"]["total_return_pct"] - cash_overlay_return
    selection_loss_pp = -selection_summary["portfolio_contribution_pp"]
    sizing_effect_pp = sizing_summary["return_effect_pp"]
    timing_reconciled_pp = active_path_gap - selection_loss_pp - sizing_effect_pp
    factor_values = [
        ("현금 대기", cash_summary["exact_overlay_effect_pp"], "무비용 경로에서 전일 현금을 KOSPI로 일별 대체", "중간"),
        ("조기 매도/타이밍", timing_reconciled_pp, f"투자경로 잔여−선정−비중; 60일 원시 민감도 {early_exit_pp:.2f}%p", "낮음"),
        ("누적 거래비용", cost_summary["exact_return_effect_pp"], "비용 0으로 전체 경로 재실행(위험게이트 상태도 재평가)", "높음"),
        ("종목 선정", selection_loss_pp, "같은 진입·매도창 KOSPI 대비 종목 초과수익의 반대부호", "중간"),
        ("포지션 비중", sizing_effect_pp, "가용현금 완전배분 무상한 시나리오−기준", "낮음"),
    ]
    explained = sum(value for _factor, value, _method, _confidence in factor_values)
    factor_values.append(("기타/미분해", gap - explained, "전체 격차−5개 진단값 단순합(중복 포함 잔차)", "낮음"))
    sorted_factors = sorted(factor_values[:-1], key=lambda item: item[1], reverse=True)
    ranks = {factor: rank for rank, (factor, *_rest) in enumerate(sorted_factors, 1)}
    factor_rows = [
        {"rank": ranks.get(factor, "-"), "factor": factor, "gap_contribution_pp": round(value, 4),
         "method": method, "confidence": confidence}
        for factor, value, method, confidence in factor_values
    ]
    factor_rows = sorted([row for row in factor_rows if row["rank"] != "-"], key=lambda row: int(row["rank"])) + [
        row for row in factor_rows if row["rank"] == "-"
    ]
    bridge_values = [
        ("stockAlarm 실제", baseline_metrics["all"]["total_return_pct"]),
        ("거래비용 되돌림", cost_summary["exact_return_effect_pp"]),
        ("현금을 KOSPI로 대체", cash_summary["exact_overlay_effect_pp"]),
        ("투자경로 잔여격차", active_path_gap),
        ("KOSPI Buy & Hold", kospi_metrics["all"]["total_return_pct"]),
    ]
    cumulative = 0.0
    bridge_rows = []
    for index, (label, value) in enumerate(bridge_values, 1):
        cumulative = value if index == 1 else (value if index == len(bridge_values) else cumulative + value)
        bridge_rows.append({"rank": index, "label": label, "value_pp": round(value, 4), "cumulative_pct": round(cumulative, 4)})
    regime_rows = []
    for scope in ("all", "bull", "bear", "sideways"):
        cash_scope = _scope_rows(missed_rows, regimes, scope)
        exit_scope = _scope_rows(post_sale_rows, regimes, scope)
        selection_scope = _scope_rows(selection_rows, regimes, scope, "signal_date")
        sell_scope = _scope_rows([row for row in baseline.events if str(row.get("event", "")).startswith("sell")], regimes, scope)
        scope_gap = kospi_metrics[scope]["total_return_pct"] - baseline_metrics[scope]["total_return_pct"]
        values = {
            "cash_pp": sum(float(row["missed_return"]) for row in cash_scope) * 100,
            "early_exit_pp": sum(float(row.get("opportunity_contribution_60d_pp") or 0) for row in exit_scope),
            "cost_pp": sum(float(row.get("transaction_cost") or 0) for row in sell_scope) / initial_cash * 100,
            "selection_pp": -sum(float(row["portfolio_contribution_pp"]) for row in selection_scope),
            "sizing_pp": unconstrained_metrics[scope]["total_return_pct"] - baseline_metrics[scope]["total_return_pct"],
        }
        regime_rows.append({
            "regime": scope, "stock_return_pct": baseline_metrics[scope]["total_return_pct"],
            "kospi_return_pct": kospi_metrics[scope]["total_return_pct"], "gap_pp": scope_gap,
            **{key: round(value, 4) for key, value in values.items()},
            "defensive_cash_pp": round(sum(float(row["missed_return"]) for row in cash_scope if row["defensive_mode"]) * 100, 4),
            "general_cash_pp": round(sum(float(row["missed_return"]) for row in cash_scope if not row["defensive_mode"]) * 100, 4),
            "residual_pp": round(scope_gap - sum(values.values()), 4),
        })
    parameters = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "actual_start_date": simulator.days[0],
        "actual_end_date": simulator.days[-1], "trading_days": len(simulator.days),
        "initial_cash": initial_cash, "allocation_pct": allocation_pct, "seed": seed,
        "score_weights": weights, "stock_return_pct": baseline_metrics["all"]["total_return_pct"],
        "kospi_return_pct": kospi_metrics["all"]["total_return_pct"], "total_gap_pp": gap,
        "post_sale_horizons": list(HORIZONS), "defensive_gate_in_baseline": True,
        "correlation_group_limit_in_baseline": True,
        "risk_halt_days": cash_summary["defensive_days"],
        "risk_halt_transitions": cash_summary["risk_halt_transitions"],
        "risk_resume_transitions": cash_summary["risk_resume_transitions"],
        "defensive_cash_effect_pp": cash_summary["defensive_overlay_effect_pp"],
        "general_cash_effect_pp": cash_summary["general_overlay_effect_pp"],
        "correlation_rejected_signals": cash_summary["correlation_rejected_signals"],
        "live_database_access": "read_only_active_weight_snapshot",
        "live_state_modified": False,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(OUTPUT_DIR / "daily_portfolio_state.csv", baseline.daily_state)
    _write_csv(OUTPUT_DIR / "trade_events.csv", baseline.events)
    _write_csv(OUTPUT_DIR / "post_sale_tracking.csv", post_sale_rows)
    _write_csv(OUTPUT_DIR / "selection_effect.csv", selection_rows)
    _write_csv(OUTPUT_DIR / "factor_contributions.csv", factor_rows)
    _write_csv(OUTPUT_DIR / "regime_contributions.csv", regime_rows)
    _write_csv(OUTPUT_DIR / "waterfall_bridge.csv", bridge_rows)
    (OUTPUT_DIR / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    create_waterfall_chart(bridge_rows, CHART_PATH)
    REPORT_PATH.write_text(build_report(parameters, factor_rows, regime_rows, bridge_rows, cash_summary, cost_summary,
                                        exit_summary, selection_summary, sizing_summary), encoding="utf-8")
    result = {
        "report": str(REPORT_PATH), "chart": str(CHART_PATH), "gap_pp": round(gap, 4),
        "top_three": [row["factor"] for row in sorted(factor_rows[:-1], key=lambda row: float(row["gap_contribution_pp"]), reverse=True)[:3]],
        "live_state_modified": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Decompose stockAlarm versus KOSPI return gap")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
