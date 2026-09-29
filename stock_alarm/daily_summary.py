from __future__ import annotations

from datetime import datetime

from .app import is_trading_day, load_env, write_error_log
from .data_store import (
    previous_virtual_valuation,
    recent_virtual_sales,
    recent_virtual_trades,
    virtual_deposits_since,
    virtual_trader_state,
)
from .notifier import send_notification
from .report import daily_ticker_rows, reconciled_daily_alert_rows, tail_csv
from .sell_check import short_reason
from .virtual_trader_report import current_prices


def latest_recommendations() -> list[dict[str, str]]:
    today = datetime.now().date().isoformat()
    return daily_ticker_rows(tail_csv("logs/recommendations.csv", 10000), today)


def latest_sell_alerts() -> list[dict[str, str]]:
    today = datetime.now().date().isoformat()
    return reconciled_daily_alert_rows(
        tail_csv("logs/sell_alerts.csv", 10000), tail_csv("logs/deliveries.csv", 10000), today, "sell"
    )


def _today(rows: list[dict]) -> list[dict]:
    prefix = datetime.now().date().isoformat()
    return [row for row in rows if str(row.get("created_at", "")).startswith(prefix)]


def _won(value: int | float) -> str:
    return f"{int(round(value)):,}원"


def _trade_lines(buys: list[dict], sales: list[dict]) -> list[str]:
    lines = ["■ 오늘 주요 거래"]
    if buys:
        row = buys[0]
        lines.append(
            f"매수: {row.get('name') or row.get('ticker')} {int(row.get('quantity') or 0)}주 · "
            f"비중 {float(row.get('allocation_pct') or 0):.0f}%"
        )
    else:
        lines.append("매수: 없음")
    if sales:
        row = sales[0]
        reason = str(row.get("reason") or "전략 조건").split(",")[0]
        lines.append(f"매도: {row.get('name') or row.get('ticker')} {int(row.get('quantity') or 0)}주 · {reason}")
    else:
        lines.append("매도: 없음")
    return lines


def unbought_recommendation_lines(recommendations: list[dict], buys: list[dict], limit: int = 5) -> list[str]:
    """Picks the virtual trader passed on -- no longer sent one by one intraday."""
    bought = {str(row.get("ticker")) for row in buys}
    names = [str(row.get("name") or row.get("ticker")) for row in recommendations if str(row.get("ticker")) not in bought]
    if not names:
        return []
    extra = f" 외 {len(names) - limit}종목" if len(names) > limit else ""
    return ["", "■ 오늘 추천(미매수)", ", ".join(names[:limit]) + extra]


def shadow_lines() -> list[str]:
    """What the real account would have ordered today (observation only)."""
    from .data_store import query_rows

    rows = query_rows(
        "SELECT side, cost FROM shadow_orders WHERE created_at LIKE ?", (f"{datetime.now().date().isoformat()}%",),
    )
    buys = [row for row in rows if row.get("side") == "BUY"]
    sells = [row for row in rows if row.get("side") == "SELL"]
    if not rows:
        return ["", "■ 실계좌였다면(섀도)", "주문 없음"]
    spent = sum(int(row.get("cost") or 0) for row in buys)
    return ["", "■ 실계좌였다면(섀도)", f"매수 {len(buys)}건 · {_won(spent)} · 매도 {len(sells)}건"]


def regime_line(labels: list[dict[str, str]]) -> str:
    """Today's KOSPI regime and what a bull label still needs (120MA + 60d rule)."""
    last = labels[-1]
    close, ma, momentum = float(last["close"]), float(last["ma120"]), float(last["return_60d_pct"])
    names = {"bull": "상승장", "bear": "하락장", "sideways": "횡보장"}
    line = f"국면 {names.get(last['regime'], last['regime'])} · KOSPI {close:,.0f} / 120일선 {ma:,.0f} ({(close / ma - 1) * 100:+.1f}%)"
    if last["regime"] != "bull":
        line += f" · 60일 {momentum:+.1f}% (상승장은 +5% 이상)"
    return line


def research_lines() -> list[str]:
    """Strategy vs the index layouts that decide the mid-November review."""
    from . import core_satellite_tracker as core30
    from .dashboard import benchmark_returns, core30_returns, regime_label_rows
    from .data_store import query_rows
    from .trading_profiles import PROFILES

    lines = ["", "■ 전략 vs 지수"]
    try:
        control = PROFILES["exp_control"]["db_path"]
        funded = int(query_rows("SELECT COALESCE(SUM(amount), 0) AS total FROM virtual_deposits", path=control)[0]["total"])
        last = query_rows("SELECT equity FROM virtual_valuation_snapshots ORDER BY snapshot_id DESC LIMIT 1", path=control)
        core, index70 = core30_returns(core30.DB_PATH), core30_returns(core30.INDEX70_DB_PATH)
        parts = [f"전략 {(int(last[0]['equity']) / funded - 1) * 100:+.1f}%"] if last and funded else []
        if core:
            parts.append(f"지수30% {list(core.values())[-1]:+.1f}%")
            kodex = benchmark_returns(sorted(core), "069500")
            if kodex:
                parts.append(f"KODEX 200 {list(kodex.values())[-1]:+.1f}%")
        if index70:
            parts.append(f"지수70%+현금 {list(index70.values())[-1]:+.1f}%({min(index70)[5:].replace('-', '/')}~)")
        if parts:
            since = f"{min(core)[5:].replace('-', '/')} 이후 · " if core else ""
            lines.append(since + " · ".join(parts))
        labels = regime_label_rows(datetime.now().date().isoformat())
        if labels:
            lines.append(regime_line(labels))
    except Exception:
        return []
    return lines if len(lines) > 2 else []


def market_comparison_line(daily_return_pct: float | None, whole_market: dict[str, str] | None) -> str | None:
    if daily_return_pct is None or not whole_market:
        return None
    gap = daily_return_pct - float(whole_market["avg_change_pct"])
    return f"계좌 대비 시장: {gap:+.2f}%p (시장 평균 {float(whole_market['avg_change_pct']):+.2f}%)"


def message() -> str:
    from .market_summary import whole_market_summary

    whole_market = whole_market_summary()
    recommendations = latest_recommendations()
    sell_alerts = latest_sell_alerts()
    buys = _today(recent_virtual_trades(500))
    sales = _today(recent_virtual_sales(500))
    prices = current_prices()
    state = virtual_trader_state(prices)
    holdings = state["holdings"]
    up = sum(float(row["return_pct"]) > 0 for row in holdings)
    down = sum(float(row["return_pct"]) < 0 for row in holdings)
    flat = len(holdings) - up - down
    today_start = datetime.now().date().isoformat()
    previous = previous_virtual_valuation(today_start)
    deposited_today = virtual_deposits_since(today_start)
    daily_return: float | None = None
    if previous:
        daily_profit = int(state["total_equity"]) - int(previous["equity"]) - deposited_today
        base = int(previous["equity"]) + deposited_today
        daily_return = daily_profit / base * 100 if base else 0.0
        daily_result = f"오늘 손익 {_won(daily_profit)} ({daily_return:+.2f}%)"
    else:
        daily_result = "오늘 손익 산정 전 (전일 평가 없음)"
    best = max(holdings, key=lambda row: float(row["return_pct"]), default=None)
    worst = min(holdings, key=lambda row: float(row["return_pct"]), default=None)
    now = datetime.now()
    lines = [
        f"[주식 마감 브리핑 | {now:%m/%d}]",
        "",
        "■ 오늘 결과",
        f"추천 {len(recommendations)}종목 · 가상매수 {len(buys)}종목 · 가상매도 {len(sales)}종목",
        f"매도 조건 충족 {len(sell_alerts)}종목(추천 추적 전체 기준)",
    ]
    if whole_market:
        lines.extend(["", "■ 오늘 시장(코스피·코스닥)", f"상승 비율: {whole_market['up_ratio_pct']}%", f"평균 등락률: {float(whole_market['avg_change_pct']):+.2f}%"])
        comparison = market_comparison_line(daily_return, whole_market)
        if comparison:
            lines.append(comparison)
    lines.extend([
        "",
        "■ 가상계좌",
        f"총자산 {_won(state['total_equity'])}",
        daily_result,
        f"누적 수익률 {float(state['total_return_pct']):+.2f}%",
        f"현금 {_won(state['cash'])} · 주식 {_won(state['holdings_value'])}",
        "",
        "■ 보유종목",
        f"전체 수익률 {float(state['holdings_return_pct']):+.2f}%",
        f"상승 {up} · 하락 {down} · 보합 {flat}",
    ])
    if best:
        lines.append(f"최고 {best.get('name') or best['ticker']} {float(best['return_pct']):+.2f}%")
        lines.append(f"최저 {worst.get('name') or worst['ticker']} {float(worst['return_pct']):+.2f}%")
    lines.append("")
    lines.extend(_trade_lines(buys, sales))
    lines.extend(unbought_recommendation_lines(recommendations, buys))
    lines.extend(shadow_lines())
    lines.extend(research_lines())
    lines.extend(["", "■ 내일 확인"])
    if sell_alerts:
        for row in sell_alerts[:2]:
            lines.append(f"{row.get('name') or row.get('ticker')} · {short_reason(row.get('reason') or '') or '매도 조건 재점검'}")
    else:
        lines.append("특이사항 없음")
    lines.extend(["", f"가격 기준 {now:%H:%M} · 가상매매 결과"])
    return "\n".join(lines)


def run() -> str:
    load_env()
    if not is_trading_day():
        return "market_closed"
    return send_notification(message())


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
