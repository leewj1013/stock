from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median


REPORT_DIR = Path("reports/backtest")
OUTPUT_DIR = REPORT_DIR / "drawdown_cause"
STATE_PATH = REPORT_DIR / "benchmark_comparison/stock_alarm_daily_state.csv"
EVENT_PATH = REPORT_DIR / "benchmark_comparison/stock_alarm_events.csv"
TRADE_PATH = REPORT_DIR / "benchmark_comparison/stock_alarm_trades.csv"
FACTOR_PATH = REPORT_DIR / "factor_samples.csv"
REGIME_PATH = REPORT_DIR / "regime_labels.csv"
METRIC_PATH = REPORT_DIR / "benchmark_comparison/strategy_metrics.csv"
PRICE_DIR = Path("data/backtest/ohlcv")
REPORT_PATH = REPORT_DIR / "DRAWDOWN_CAUSE_ANALYSIS_REPORT.md"
CHART_PATH = REPORT_DIR / "DRAWDOWN_CAUSE_TIMELINE.png"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def define_drawdown_window(states: list[dict], claimed_trigger: str = "2022-12-28") -> dict:
    ordered = sorted(states, key=lambda row: row["date"])
    cutoff_index = next(index for index, row in enumerate(ordered) if row["date"] == claimed_trigger)
    prior = ordered[: cutoff_index + 1]
    peak = max(prior, key=lambda row: f(row["equity"]))
    peak_index = ordered.index(peak)
    actual = next(
        (row for row in ordered[peak_index:] if "drawdown_limit" in row.get("risk_reason", "")),
        None,
    )
    first_decline = next(
        (row for row in ordered[peak_index + 1:] if f(row["equity"]) < f(peak["equity"])),
        None,
    )
    return {
        "peak_date": peak["date"], "peak_equity": f(peak["equity"]),
        "decline_start_date": first_decline["date"] if first_decline else "",
        "claimed_trigger_date": claimed_trigger,
        "claimed_trigger_equity": f(ordered[cutoff_index]["equity"]),
        "claimed_trigger_drawdown_pct": f(ordered[cutoff_index]["drawdown_pct"]),
        "claimed_trigger_reason": ordered[cutoff_index].get("risk_reason", ""),
        "actual_drawdown_trigger_date": actual["date"] if actual else "",
        "actual_drawdown_trigger_equity": f(actual["equity"]) if actual else 0.0,
        "actual_drawdown_trigger_pct": f(actual["drawdown_pct"]) if actual else 0.0,
        "actual_drawdown_trigger_reason": actual.get("risk_reason", "") if actual else "",
    }


def interval_rows(rows: list[dict], start: str, end: str, include_start: bool = True) -> list[dict]:
    return [row for row in rows if (row["date"] >= start if include_start else row["date"] > start) and row["date"] <= end]


def score_lookup(factors: list[dict]) -> dict[tuple[str, str], dict]:
    keys = ("volume_score", "trading_value_score", "trend_score", "relative_strength_score",
            "news_score", "disclosure_score", "financial_score")
    output = {}
    for row in factors:
        output[(row["signal_date"], row["ticker"])] = {
            **{key: f(row.get(key)) for key in keys},
            "score": sum(f(row.get(key)) for key in keys),
        }
    return output


def enrich_events(events: list[dict], scores: dict[tuple[str, str], dict], start: str, end: str) -> list[dict]:
    output = []
    for row in interval_rows(events, start, end, include_start=False):
        score = scores.get((row.get("signal_date", ""), row.get("ticker", "")), {})
        entry = f(row.get("entry_price"))
        shares, price, cost = f(row.get("shares")), f(row.get("price")), f(row.get("transaction_cost"))
        realized = (price - entry) * shares - cost if row.get("event", "").startswith("sell") else 0.0
        return_pct = ((price / entry - 1) * 100) if entry and row.get("event", "").startswith("sell") else None
        output.append({
            "date": row["date"], "event": row["event"], "ticker": row["ticker"], "name": row["name"],
            "signal_date": row.get("signal_date", ""), "entry_date": row.get("entry_date", ""),
            "entry_price": entry or "", "price": price, "shares": int(shares),
            "gross_notional": f(row.get("gross_notional")), "transaction_cost": cost,
            "realized_pnl": round(realized, 4), "sale_return_pct": "" if return_pct is None else round(return_pct, 4),
            "portfolio_weight_pct": round(f(row.get("gross_notional")) / f(row.get("portfolio_equity_before"), 1) * 100, 4),
            "reason": row.get("reason", ""), "score": round(score.get("score", 0.0), 4),
            "volume_score": round(score.get("volume_score", 0.0), 4),
            "trading_value_score": round(score.get("trading_value_score", 0.0), 4),
            "trend_score": round(score.get("trend_score", 0.0), 4),
            "relative_strength_score": round(score.get("relative_strength_score", 0.0), 4),
        })
    return output


def loss_contributions(states: list[dict], events: list[dict], start: str, end: str) -> tuple[list[dict], dict]:
    by_day = {row["date"]: row for row in states}
    start_values = json.loads(by_day[start]["position_values_json"])
    end_values = json.loads(by_day[end]["position_values_json"])
    flows = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "cost": 0.0, "name": "", "events": 0})
    for row in interval_rows(events, start, end, include_start=False):
        bucket = flows[row["ticker"]]
        bucket["name"] = row.get("name", row["ticker"])
        bucket["events"] += 1
        if row["event"] == "buy":
            bucket["buy"] += f(row["gross_notional"])
        elif row["event"].startswith("sell"):
            bucket["sell"] += f(row["gross_notional"])
            bucket["cost"] += f(row["transaction_cost"])
    tickers = set(start_values) | set(end_values) | set(flows)
    rows = []
    for ticker in tickers:
        bucket = flows[ticker]
        pnl = f(end_values.get(ticker)) - f(start_values.get(ticker)) + bucket["sell"] - bucket["buy"] - bucket["cost"]
        rows.append({
            "ticker": ticker, "name": bucket["name"] or ticker,
            "start_value": round(f(start_values.get(ticker)), 4), "buys": round(bucket["buy"], 4),
            "sells": round(bucket["sell"], 4), "costs": round(bucket["cost"], 4),
            "end_value": round(f(end_values.get(ticker)), 4), "pnl_contribution": round(pnl, 4),
            "pnl_pct_of_peak": round(pnl / f(by_day[start]["equity"]) * 100, 4), "event_count": bucket["events"],
        })
    rows.sort(key=lambda row: row["pnl_contribution"])
    equity_change = f(by_day[end]["equity"]) - f(by_day[start]["equity"])
    contribution_sum = sum(f(row["pnl_contribution"]) for row in rows)
    return rows, {
        "equity_change": equity_change, "contribution_sum": contribution_sum,
        "residual": equity_change - contribution_sum,
    }


def classify_reason(reason: str) -> str:
    if "손절" in reason or "ATR" in reason:
        return "손절"
    if "20일선" in reason:
        return "20일선이탈"
    if "익절" in reason:
        return "분할/목표익절"
    if "반납" in reason:
        return "수익반납"
    if "보유" in reason or "기간" in reason:
        return "기간청산"
    return "기타"


def sale_reason_summary(events: list[dict], start: str, end: str) -> list[dict]:
    buckets = defaultdict(list)
    for row in interval_rows(events, start, end, include_start=False):
        if not row["event"].startswith("sell"):
            continue
        entry, price, shares, cost = f(row["entry_price"]), f(row["price"]), f(row["shares"]), f(row["transaction_cost"])
        pnl = (price - entry) * shares - cost
        buckets[classify_reason(row.get("reason", ""))].append((pnl, (price / entry - 1) * 100 if entry else 0.0))
    total_loss = abs(sum(pnl for values in buckets.values() for pnl, _ in values if pnl < 0)) or 1.0
    rows = []
    for reason, values in buckets.items():
        losses = [pnl for pnl, _ in values if pnl < 0]
        rows.append({
            "reason": reason, "sale_events": len(values), "loss_events": len(losses),
            "realized_pnl": round(sum(pnl for pnl, _ in values), 4),
            "loss_amount": round(sum(losses), 4), "share_of_all_realized_losses_pct": round(abs(sum(losses)) / total_loss * 100, 4),
            "average_sale_return_pct": round(mean(ret for _, ret in values), 4),
            "median_sale_return_pct": round(median(ret for _, ret in values), 4),
        })
    return sorted(rows, key=lambda row: row["realized_pnl"])


def recurrence_summary(events: list[dict], start: str, end: str) -> list[dict]:
    periods = {"낙폭구간": lambda day: start < day <= end, "이후기간": lambda day: day > end}
    rows = []
    for label, predicate in periods.items():
        buckets = defaultdict(list)
        for row in events:
            if not row["event"].startswith("sell") or not predicate(row["date"]):
                continue
            entry, price = f(row["entry_price"]), f(row["price"])
            ret = (price / entry - 1) * 100 if entry else 0.0
            pnl = (price - entry) * f(row["shares"]) - f(row["transaction_cost"])
            buckets[classify_reason(row.get("reason", ""))].append((ret, pnl))
        for reason, values in buckets.items():
            rows.append({"period": label, "reason": reason, "events": len(values),
                         "average_return_pct": round(mean(x[0] for x in values), 4),
                         "loss_rate_pct": round(sum(x[1] < 0 for x in values) / len(values) * 100, 4),
                         "realized_pnl": round(sum(x[1] for x in values), 4)})
    return rows


def load_price_returns(start: str, end: str) -> tuple[dict, list[dict]]:
    start_key, end_key = start.replace("-", ""), end.replace("-", "")
    market = {}
    breadth_by_day = defaultdict(list)
    for path in PRICE_DIR.glob("*.csv"):
        rows = read_csv(path)
        if not rows:
            continue
        by_date = {row["date"]: row for row in rows}
        if start_key in by_date and end_key in by_date:
            market[path.stem] = (f(by_date[end_key]["close"]) / f(by_date[start_key]["close"]) - 1) * 100
        if path.stem == "KOSPI":
            continue
        previous = None
        for row in rows:
            close = f(row["close"])
            if previous and start_key <= row["date"] <= end_key:
                breadth_by_day[row["date"]].append(close > previous)
            previous = close
    breadth = []
    for day, values in sorted(breadth_by_day.items()):
        ratio = sum(values) / len(values) if values else 0.0
        mode = "공격" if ratio >= .60 else "중립" if ratio >= .45 else "방어"
        breadth.append({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "up_ratio_pct": round(ratio * 100, 4),
                        "market_filter_mode": mode, "stocks": len(values)})
    return market, breadth


def exposure_matched_kospi_return(states: list[dict], start: str, end: str) -> float:
    prices = read_csv(PRICE_DIR / "KOSPI.csv")
    closes = {f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:]}": f(row["close"]) for row in prices}
    scoped = interval_rows(states, start, end)
    wealth = 1.0
    for previous, current in zip(scoped, scoped[1:]):
        if previous["date"] not in closes or current["date"] not in closes or not closes[previous["date"]]:
            continue
        market_return = closes[current["date"]] / closes[previous["date"]] - 1
        wealth *= 1 + f(previous.get("invested_weight")) * market_return
    return (wealth - 1) * 100


def score_comparison(trades: list[dict], scores: dict[tuple[str, str], dict], start: str, end: str) -> list[dict]:
    buckets = {"낙폭구간 진입": [], "그 외 기간 진입": []}
    for row in trades:
        score = scores.get((row["signal_date"], row["ticker"]), {}).get("score")
        if score is None:
            continue
        label = "낙폭구간 진입" if start < row["entry_date"] <= end else "그 외 기간 진입"
        buckets[label].append(score)
    return [{"period": label, "trades": len(values), "mean_score": round(mean(values), 4) if values else "",
             "median_score": round(median(values), 4) if values else "", "min_score": round(min(values), 4) if values else "",
             "max_score": round(max(values), 4) if values else ""} for label, values in buckets.items()]


def create_chart(states: list[dict], contributions: list[dict], start: str, end: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(OUTPUT_DIR / ".matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    scoped = interval_rows(states, start, end)
    days = [date.fromisoformat(row["date"]) for row in scoped]
    equity = [f(row["equity"]) / 1_000_000 for row in scoped]
    kospi_rows = read_csv(PRICE_DIR / "KOSPI.csv")
    kospi = {f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:]}": f(row["close"]) for row in kospi_rows}
    base = kospi.get(start, 1.0)
    indexed = [kospi.get(row["date"], base) / base * equity[0] for row in scoped]
    worst = contributions[:10]
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), gridspec_kw={"height_ratios": [1.25, 1]})
    axes[0].plot(days, equity, color="#2457A6", linewidth=2, label="stockAlarm equity")
    axes[0].plot(days, indexed, color="#D07A1E", linewidth=1.8, linestyle="--", label="KOSPI indexed")
    axes[0].axhline(equity[0] * .9, color="#444444", linewidth=1, linestyle=":", label="-10% boundary")
    axes[0].set_title("Account equity and KOSPI during the drawdown")
    axes[0].set_ylabel("KRW millions (KOSPI indexed)")
    axes[0].grid(axis="y", color="#DDDDDD", linewidth=.6)
    axes[0].legend(frameon=False, ncol=3)
    axes[0].xaxis.set_major_locator(mdates.MonthLocator())
    axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    labels = [row["name"] for row in reversed(worst)]
    values = [f(row["pnl_contribution"]) / 1_000_000 for row in reversed(worst)]
    axes[1].barh(labels, values, color="#D07A1E", edgecolor="#7A4210")
    axes[1].axvline(0, color="#333333", linewidth=.8)
    axes[1].set_title("Largest ticker loss contributions")
    axes[1].set_xlabel("KRW millions; exact peak-to-trigger contribution")
    axes[1].grid(axis="x", color="#E5E5E5", linewidth=.6)
    fig.tight_layout()
    CHART_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHART_PATH, dpi=160, bbox_inches="tight")
    plt.close(fig)


def md_table(rows: list[dict], fields: list[tuple[str, str]], limit: int | None = None) -> str:
    selected = rows[:limit] if limit else rows
    header = "| " + " | ".join(label for _, label in fields) + " |"
    divider = "|" + "|".join("---" for _ in fields) + "|"
    body = ["| " + " | ".join(str(row.get(key, "")) for key, _ in fields) + " |" for row in selected]
    return "\n".join([header, divider, *body])


def run() -> dict:
    states, events, trades = read_csv(STATE_PATH), read_csv(EVENT_PATH), read_csv(TRADE_PATH)
    factors = read_csv(FACTOR_PATH)
    window = define_drawdown_window(states)
    start, requested_end, actual_end = window["peak_date"], window["claimed_trigger_date"], window["actual_drawdown_trigger_date"]
    scores = score_lookup(factors)
    timeline = enrich_events(events, scores, start, actual_end)
    contributions, reconciliation = loss_contributions(states, events, start, actual_end)
    reasons = sale_reason_summary(events, start, actual_end)
    recurrence = recurrence_summary(events, start, actual_end)
    score_rows = score_comparison(trades, scores, start, actual_end)
    market_returns, breadth = load_price_returns(start, actual_end)
    regimes = Counter(row["regime"] for row in read_csv(REGIME_PATH) if start <= row["date"] <= actual_end)
    state_window = interval_rows(states, start, actual_end)
    breadth_counts = Counter(row["market_filter_mode"] for row in breadth)
    breadth_map = {row["date"]: row["market_filter_mode"] for row in breadth}
    buys_by_mode = Counter(breadth_map.get(row["date"], "미분류") for row in timeline if row["event"] == "buy")
    loss_rows = [row for row in contributions if f(row["pnl_contribution"]) < 0]
    total_loss_abs = abs(sum(f(row["pnl_contribution"]) for row in loss_rows)) or 1
    top5_share = abs(sum(f(row["pnl_contribution"]) for row in loss_rows[:5])) / total_loss_abs * 100
    hhi = sum((abs(f(row["pnl_contribution"])) / total_loss_abs) ** 2 for row in loss_rows)
    benchmark_context = [row for row in read_csv(METRIC_PATH) if row["regime"] == "bear" and row["strategy"] in {"stock_alarm", "momentum", "random_median"}]

    write_csv(OUTPUT_DIR / "drawdown_transaction_timeline.csv", timeline)
    write_csv(OUTPUT_DIR / "ticker_loss_contributions.csv", contributions)
    write_csv(OUTPUT_DIR / "sell_reason_decomposition.csv", reasons)
    write_csv(OUTPUT_DIR / "score_distribution_comparison.csv", score_rows)
    write_csv(OUTPUT_DIR / "market_breadth_timeline.csv", breadth)
    write_csv(OUTPUT_DIR / "recurrence_by_sell_reason.csv", recurrence)
    create_chart(states, contributions, start, actual_end)

    params = {
        **window, "analysis_end_date": actual_end, "requested_cutoff_retained": requested_end,
        "source_paths": [str(STATE_PATH), str(EVENT_PATH), str(TRADE_PATH), str(FACTOR_PATH), str(REGIME_PATH)],
        "no_backtest_rerun": True, "live_state_modified": False,
        "reconciliation": reconciliation,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "parameters.json").write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")

    peak = window["peak_equity"]
    end_equity = window["actual_drawdown_trigger_equity"]
    kospi_return = market_returns.get("KOSPI")
    regime_total = sum(regimes.values()) or 1
    avg_cash = mean(f(row["cash_weight"]) for row in state_window) * 100
    avg_exposure = mean(f(row["invested_weight"]) for row in state_window) * 100
    exposure_matched_return = exposure_matched_kospi_return(states, start, actual_end)
    actual_return = (end_equity / peak - 1) * 100
    selection_timing_gap = actual_return - exposure_matched_return
    sale_count = sum(int(row["sale_events"]) for row in reasons)
    stop_row = next((row for row in reasons if row["reason"] == "손절"), {})
    ma_row = next((row for row in reasons if row["reason"] == "20일선이탈"), {})
    time_row = next((row for row in reasons if row["reason"] == "기간청산"), {})
    total_cost = sum(f(row.get("transaction_cost")) for row in timeline)
    cost_share_of_drawdown = total_cost / abs(reconciliation["equity_change"]) * 100
    window_score = next(row for row in score_rows if row["period"] == "낙폭구간 진입")
    other_score = next(row for row in score_rows if row["period"] == "그 외 기간 진입")
    score_gap = f(window_score["mean_score"]) - f(other_score["mean_score"])
    report = f"""# 2022년 계좌 낙폭 원인 진단

## 기술 요약

계좌 최고점은 **{start} {peak:,.0f}원**입니다. 사용자 지정일인 2022-12-28에는 낙폭이 **{window['claimed_trigger_drawdown_pct']:.2f}%**로 아직 -10%를 넘지 않았고, 중단 사유는 `exposure_limit`이었습니다. 실제 `drawdown_limit` 최초 발동은 **{actual_end}**, 자산 **{end_equity:,.0f}원**, 고점 대비 **{window['actual_drawdown_trigger_pct']:.2f}%**였습니다. 따라서 원인 구간은 {start} 장 마감 이후부터 {actual_end}까지로 분석했습니다.

이 구간의 계좌 손실은 **{reconciliation['equity_change']:,.0f}원**이며 종목별 현금흐름 방식으로 **{reconciliation['contribution_sum']:,.0f}원**을 설명합니다. 잔차는 **{reconciliation['residual']:,.2f}원**입니다. 손실 종목 Top 5가 전체 음(-)의 종목 기여도의 **{top5_share:.1f}%**를 차지했고 HHI는 **{hhi:.3f}**로, 손실이 완전히 한 종목에만 집중된 사건이라기보다 여러 포지션 손실이 누적된 형태인지 아래 표에서 확인할 수 있습니다.

## 낙폭 날짜는 12월 28일과 29일을 구분해야 한다

| 항목 | 날짜 | 계좌자산 | 고점대비 낙폭 | 판정 사유 |
|---|---:|---:|---:|---|
| 최고점 | {start} | {peak:,.0f}원 | 0.00% | 당시 보유비중 상한 초과 |
| 하락 시작 | {window['decline_start_date']} | - | - | 최고점 이후 첫 하락일 |
| 사용자 지정 트리거일 | {requested_end} | {window['claimed_trigger_equity']:,.0f}원 | {window['claimed_trigger_drawdown_pct']:.2f}% | {window['claimed_trigger_reason']} |
| 실제 -10% 낙폭 트리거 | {actual_end} | {end_equity:,.0f}원 | {window['actual_drawdown_trigger_pct']:.2f}% | {window['actual_drawdown_trigger_reason']} |

![계좌 낙폭과 손실 기여 종목]({CHART_PATH.name})

위 차트 상단은 계좌와 KOSPI를 같은 시작값으로 지수화해 비교하고, 하단은 정확한 현금흐름 항등식으로 계산한 종목별 손실 기여도입니다. 차트의 KOSPI는 종목선정·비중 차이를 제거한 시장 방향 비교이지 투자 가능성을 가정한 반사실적 수익률이 아닙니다.

## 손실은 어떤 종목에서 발생했는가

{md_table(contributions, [('ticker','종목코드'),('name','종목명'),('start_value','시작평가액'),('buys','추가매수'),('sells','매도대금'),('costs','비용'),('end_value','종료평가액'),('pnl_contribution','손익기여'),('pnl_pct_of_peak','고점대비 %p')], 20)}

손실 집중도는 Top 5 비중 {top5_share:.1f}%, HHI {hhi:.3f}입니다. `pnl_contribution`은 종료평가액-시작평가액+매도대금-매수대금-비용으로 계산되어 계좌 자산 변화와 직접 합산됩니다.

## 매도 사유별로 손실이 어떻게 확정됐는가

{md_table(reasons, [('reason','매도사유'),('sale_events','매도건'),('loss_events','손실건'),('realized_pnl','실현손익'),('loss_amount','손실액'),('share_of_all_realized_losses_pct','전체 실현손실 비중%'),('average_sale_return_pct','평균 매도수익률%'),('median_sale_return_pct','중앙값%')])}

손절의 빈도와 체결수익률은 위 표의 `손절` 행으로 판단합니다. 단일 매도 이벤트 단위이므로 1차 부분익절과 잔량매도는 별도 이벤트로 집계되며, 종목 전체 생애수익과 혼동하지 않아야 합니다.

## 진입 점수는 평소보다 낮았는가

{md_table(score_rows, [('period','비교구간'),('trades','거래수'),('mean_score','평균점수'),('median_score','중앙값'),('min_score','최소'),('max_score','최대')])}

점수는 당시 저장된 7개 원자요인을 운영 가중치 1.0으로 합산했습니다. 뉴스·공시·재무는 point-in-time 스냅샷이 없어 0점이므로 기술요인 중심 비교입니다. 평균 차이는 인과효과가 아니라 당시 진입 품질의 기술통계입니다.

## 시장은 어려웠지만 시장필터가 포지션 크기에 연결되지는 않았다

- KOSPI 수익률: **{kospi_return:.2f}%** ({start}~{actual_end})
- 계좌 수익률: **{actual_return:.2f}%**
- 실제 일별 투자비중으로 KOSPI만 보유했다고 가정한 노출조정 수익률: **{exposure_matched_return:.2f}%**
- 실제 계좌와 노출조정 KOSPI의 차이: **{selection_timing_gap:.2f}%p**
- 국면 일수: {', '.join(f'{key} {value}일({value/regime_total*100:.1f}%)' for key,value in sorted(regimes.items()))}
- 관심종목 상승비율 기반 모드: {', '.join(f'{key} {value}일' for key,value in sorted(breadth_counts.items()))}
- 실제 신규매수 발생일의 계산상 모드: {', '.join(f'{key} {value}건' for key,value in sorted(buys_by_mode.items()))}
- 평균 현금비중 {avg_cash:.2f}%, 평균 투자비중 {avg_exposure:.2f}%

중요한 구현 사실은 이 백테스트의 `PortfolioSimulator`가 상승종목비율 모드를 신규매수 한도에 연결하지 않았다는 점입니다. 따라서 위 공격/중립/방어 값은 당시 시장이 보낸 신호를 사후 재구성한 것이며, 실제 백테스트 포지션 축소의 원인이 아닙니다. KOSDAQ 지수 일별 원천은 기존 격리 데이터에 없어 별도 수치로 제시하지 않습니다.

## 동일 시기 벤치마크 비교에서 확인 가능한 범위

동일구간 일별 경로가 보존된 것은 stockAlarm과 KOSPI뿐입니다. 랜덤·단순모멘텀은 기존 파일에 국면 합계만 남아 있어, 새 백테스트를 돌리지 않는다는 안전조건에 따라 아래는 **전체 하락장 174일의 참고값**이며 {start}~{actual_end} 정확 비교값이 아닙니다.

{md_table(benchmark_context, [('strategy','전략'),('regime','범위'),('trading_days','일수'),('trades','거래수'),('total_return_pct','총수익률%'),('mdd_pct','MDD%'),('sharpe','Sharpe')])}

따라서 “시장 자체”와 “stockAlarm 고유 문제”의 정량 분리는 KOSPI 동일구간 비교까지는 확정적이지만 랜덤·모멘텀 동일구간 차이는 미확정입니다.

## 같은 손실 패턴은 이후에도 반복됐는가

{md_table(recurrence, [('period','기간'),('reason','매도사유'),('events','건수'),('average_return_pct','평균 매도수익률%'),('loss_rate_pct','손실비율%'),('realized_pnl','실현손익')])}

낙폭구간과 이후기간의 같은 매도사유를 동일한 이벤트 정의로 비교했습니다. 이후에도 같은 사유의 손실비율과 평균수익률이 나쁘다면 구조적 반복 가능성이 높고, 이 구간에서만 악화됐다면 시장 국면 의존성이 더 큽니다.

## 종합 원인 판정

**이번 낙폭은 시장 하락과 설계상 취약성이 함께 만든 결과로 판정됩니다.** KOSPI도 같은 기간 {kospi_return:.2f}% 하락해 시장 자체가 어려웠지만, 계좌는 평균 투자비중이 {avg_exposure:.2f}%에 불과했는데도 {actual_return:.2f}% 하락했습니다. 동일 일별 투자비중으로 KOSPI를 보유한 단순 노출조정 경로는 {exposure_matched_return:.2f}%이므로 실제 계좌가 **{abs(selection_timing_gap):.2f}%p 더 나빴습니다.** 이는 시장 방향만으로는 손실 전부를 설명할 수 없고 종목선정·진입/청산 타이밍이 추가 손실을 만들었다는 진단 증거입니다. 다만 이는 귀속 분석이지 개별 요인의 인과효과 추정은 아닙니다.

**손실은 소수 종목 꼬리위험보다 다수 거래 누적형입니다.** 손실 Top 5 비중은 {top5_share:.1f}%, HHI는 {hhi:.3f}입니다. 20일선 이탈은 전체 {sale_count}개 매도 이벤트 중 {int(f(ma_row.get('sale_events')))}건이고 순실현손익 {f(ma_row.get('realized_pnl')):,.0f}원으로 가장 큰 손실 통로였습니다. 손절은 {int(f(stop_row.get('sale_events')))}건에 불과하지만 평균 {f(stop_row.get('average_sale_return_pct')):.2f}%에서 실행돼 과도한 발동 빈도보다는 손실이 깊어진 뒤 확정되는 성격이 강합니다. 기간청산도 {int(f(time_row.get('sale_events')))}건 모두 손실로 끝났습니다. 거래비용은 총 {total_cost:,.0f}원, 순낙폭의 {cost_share_of_drawdown:.1f}%로 단독 주원인은 아니지만 무시할 수 없는 보조 손실요인입니다.

**낮은 점수 종목을 잘못 허용한 문제는 아닙니다.** 낙폭구간 진입 평균점수는 {f(window_score['mean_score']):.2f}로 다른 기간보다 {score_gap:+.2f}점 높았습니다. 즉 당시 점수는 정상 또는 더 높았지만 미래수익을 구분하지 못했습니다. 또한 계산상 방어모드인 날에도 신규매수 {buys_by_mode.get('방어', 0)}건이 발생했습니다. 이 백테스트에서는 시장 breadth 필터가 포지션 한도에 연결되지 않았으므로 위험 신호가 실제 진입 축소로 전달되지 않았습니다.

**재발성은 데이터 구조상 확정할 수 없습니다.** 12월 29일 이후 장기 잠금 때문에 이후 매도 표본이 {sum(int(row['events']) for row in recurrence if row['period'] == '이후기간')}건뿐입니다. 이후기간 비교가 작다는 사실 자체가 전략 개선의 증거가 아니라 거래가 중단돼 관측할 기회가 사라진 결과입니다. 따라서 이 구간의 20일선·손절 패턴이 전체 기간에 구조적으로 반복되는지에 대한 판정은 `미확정`으로 남깁니다.

자동으로 전략이나 운영 설정을 변경하지 않았습니다.

## 검증과 재현성

- 일별 계좌 원장: 전체 {len(states):,}행·고유 거래일 {len(set(row['date'] for row in states)):,}일, 분석구간 {len(state_window):,}거래일
- 분석구간 이벤트: 매수 {sum(row['event'] == 'buy' for row in timeline):,}건, 매도 {sum(row['event'].startswith('sell') for row in timeline):,}건(총 {len(timeline):,}건)
- 손익기여 종목: {len(contributions):,}개(음의 기여 {sum(f(row['pnl_contribution']) < 0 for row in contributions):,}개, 양의 기여 {sum(f(row['pnl_contribution']) > 0 for row in contributions):,}개)
- 종목별 손익기여 합계와 실제 계좌변화의 잔차: {reconciliation['residual']:.8f}원
- 기존 CSV만 읽었으며 백테스트 재실행 없음, 운영 상태 변경 없음

확정 가능한 한계는 세 가지입니다. 첫째, 12월 28일은 -10% 낙폭 발동일이 아니라 보유비중 중단일이고 실제 낙폭 발동일은 12월 29일입니다. 둘째, 과거 뉴스·공시·재무 점수는 존재하지 않습니다. 셋째, 랜덤·모멘텀의 동일구간 일별 경로와 KOSDAQ 지수는 저장되지 않아 그 부분은 미확정입니다.
"""
    REPORT_PATH.write_text(report, encoding="utf-8")
    return {"report": str(REPORT_PATH), "chart": str(CHART_PATH), "window": window,
            "reconciliation": reconciliation, "live_state_modified": False, "backtest_rerun": False}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
