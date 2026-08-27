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


def message() -> str:
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
        f"매도 검토 {len(sell_alerts)}종목",
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
    ]
    if best:
        lines.append(f"최고 {best.get('name') or best['ticker']} {float(best['return_pct']):+.2f}%")
        lines.append(f"최저 {worst.get('name') or worst['ticker']} {float(worst['return_pct']):+.2f}%")
    lines.append("")
    lines.extend(_trade_lines(buys, sales))
    lines.extend(["", "■ 내일 확인"])
    if sell_alerts:
        for row in sell_alerts[:2]:
            lines.append(f"{row.get('name') or row.get('ticker')} · {row.get('reason') or '매도 조건 재점검'}")
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
