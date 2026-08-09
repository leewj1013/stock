from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from .app import (
    env_float,
    is_market_alert_time,
    latest_naver_trading_day,
    latest_sell_alert_times,
    load_env,
    naver_rows,
    parse_time,
    stock_name,
    write_error_log,
)
from .data_store import position_id


POSITIONS_PATH = "data/positions.csv"
SELL_ALERTS_LOG = "logs/sell_alerts.csv"
POSITIONS_REPORT_LOG = "logs/positions_report.csv"


@dataclass(frozen=True)
class SellAlert:
    ticker: str
    name: str
    entry_price: int
    close: int
    return_pct: float
    reason: str


def read_positions(path: str = POSITIONS_PATH) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as file:
        return [row for row in csv.DictReader(file) if row.get("ticker") and row.get("entry_price")]


def check_position(
    position: dict[str, str],
    end_day: date,
    previous_return: float | None = None,
    max_return: float | None = None,
    price_rows: list[list] | None = None,
) -> SellAlert | None:
    ticker = position["ticker"].strip()
    entry_price = int(float(position["entry_price"]))
    rows = price_rows if price_rows is not None else naver_rows(ticker, end_day - timedelta(days=90), end_day)
    if len(rows) < 20 or entry_price <= 0:
        return None

    closes = [int(row[4]) for row in rows[-20:]]
    close = closes[-1]
    ma20 = mean(closes)
    return_pct = (close - entry_price) / entry_price * 100
    stop_loss_pct = -abs(env_float("SELL_LOSS_PCT", 5))
    reasons: list[str] = []
    if return_pct <= stop_loss_pct:
        reasons.append(f"손절 기준 {stop_loss_pct:.1f}% 이탈")
    if close < ma20:
        reasons.append("20일선 이탈")
    if previous_return is not None:
        drop = previous_return - return_pct
        if drop >= env_float("SELL_DROP_PCT", 3):
            reasons.append(f"직전 평가 대비 수익률 {drop:.1f}%p 악화")
    if max_return is not None and max_return >= env_float("SELL_PROTECT_PROFIT_PCT", 5):
        giveback = max_return - return_pct
        if giveback >= env_float("SELL_GIVEBACK_PCT", 4):
            reasons.append(f"고점 수익률 {max_return:.1f}% 대비 {giveback:.1f}%p 반납")
    if not reasons:
        return None

    name = stock_name(ticker, position.get("name", ticker).strip() or ticker)
    return SellAlert(ticker, name, entry_price, close, return_pct, ", ".join(reasons))


def _position_returns(path: str, maximum: bool) -> dict[str, float]:
    if not os.path.exists(path):
        return {}
    result: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            key = (row.get("position_id") or "").strip()
            if not key:
                continue
            value = float(row.get("return_pct") or 0)
            result[key] = max(result.get(key, float("-inf")), value) if maximum else value
    return result


def previous_returns(path: str = POSITIONS_REPORT_LOG) -> dict[str, float]:
    return _position_returns(path, maximum=False)


def max_returns(path: str = POSITIONS_REPORT_LOG) -> dict[str, float]:
    return _position_returns(path, maximum=True)


def alerted_tickers(path: str = SELL_ALERTS_LOG) -> set[str]:
    return set(latest_sell_alert_times(path))


def position_was_alerted(position: dict[str, str], path: str = SELL_ALERTS_LOG) -> bool:
    ticker = position.get("ticker", "").strip()
    sell_time = latest_sell_alert_times(path).get(ticker)
    if not sell_time:
        return False
    entry_time = parse_time(position.get("entry_date", ""))
    # Legacy positions without an entry date retain the conservative old behavior.
    return entry_time is None or entry_time <= sell_time


def position_snapshot(
    position: dict[str, str],
    end_day: date,
    previous_return: float | None = None,
    max_return: float | None = None,
) -> tuple[SellAlert | None, dict]:
    ticker = position["ticker"].strip()
    entry_price = int(float(position["entry_price"]))
    rows = naver_rows(ticker, end_day - timedelta(days=90), end_day)
    base = {
        "position_id": position_id(position),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "name": position.get("name", ticker),
        "entry_date": position.get("entry_date", ""),
        "entry_price": entry_price,
        "previous_return_pct": previous_return,
        "max_return_pct": max_return,
        "stop_loss_triggered": 0,
        "ma20_break_triggered": 0,
        "return_drop_triggered": 0,
        "giveback_triggered": 0,
        "decision": "NO_DATA",
        "reasons": "insufficient_history",
    }
    if len(rows) < 20 or entry_price <= 0:
        return None, base

    closes = [int(row[4]) for row in rows[-20:]]
    close = closes[-1]
    ma20 = mean(closes)
    return_pct = (close - entry_price) / entry_price * 100
    stop_triggered = return_pct <= -abs(env_float("SELL_LOSS_PCT", 5))
    ma20_triggered = close < ma20
    drop_triggered = previous_return is not None and previous_return - return_pct >= env_float("SELL_DROP_PCT", 3)
    giveback_triggered = max_return is not None and max_return >= env_float("SELL_PROTECT_PROFIT_PCT", 5) and max_return - return_pct >= env_float("SELL_GIVEBACK_PCT", 4)
    alert = check_position(position, end_day, previous_return, max_return, rows)
    entry_date = position.get("entry_date", "")[:10]
    try:
        holding_days = (end_day - datetime.fromisoformat(entry_date).date()).days
    except ValueError:
        holding_days = None
    return alert, {
        **base,
        "name": alert.name if alert else position.get("name", ticker),
        "close": close,
        "holding_days": holding_days,
        "return_pct": return_pct,
        "drawdown_from_peak_pct": None if max_return is None else max_return - return_pct,
        "ma20": ma20,
        "distance_ma20_pct": (close / ma20 - 1) * 100 if ma20 else None,
        "stop_loss_triggered": int(stop_triggered),
        "ma20_break_triggered": int(ma20_triggered),
        "return_drop_triggered": int(drop_triggered),
        "giveback_triggered": int(giveback_triggered),
        "decision": "SELL" if alert else "HOLD",
        "reasons": alert.reason if alert else "",
    }


def find_alerts(positions: list[dict[str, str]], end_day: date, run_id: str | None = None) -> list[SellAlert]:
    previous = previous_returns()
    best = max_returns()
    alerts: list[SellAlert] = []
    snapshots: list[dict] = []
    for position in positions:
        ticker = position["ticker"].strip()
        key = position_id(position)
        if position_was_alerted(position):
            if run_id:
                snapshots.append({
                    "position_id": key,
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                    "ticker": ticker,
                    "name": position.get("name", ticker),
                    "entry_date": position.get("entry_date", ""),
                    "entry_price": int(float(position["entry_price"])),
                    "stop_loss_triggered": 0,
                    "ma20_break_triggered": 0,
                    "return_drop_triggered": 0,
                    "giveback_triggered": 0,
                    "decision": "ALREADY_ALERTED",
                    "reasons": "sell_alert_after_entry",
                })
            continue
        if run_id:
            alert, snapshot = position_snapshot(position, end_day, previous.get(key), best.get(key))
            snapshots.append(snapshot)
        else:
            alert = check_position(position, end_day, previous.get(key), best.get(key))
        if alert:
            alerts.append(alert)
    if run_id:
        from .data_store import write_position_checks

        write_position_checks(run_id, snapshots)
    return alerts


def alert_summary(alert: SellAlert) -> str:
    if "반납" in alert.reason:
        return "고점 대비 수익 반납"
    if alert.return_pct <= -abs(env_float("SELL_LOSS_PCT", 5)):
        return f"손실 {alert.return_pct:.1f}%"
    if "20일선" in alert.reason:
        return "20일선 이탈"
    return "수익률 악화"


def format_message(alerts: list[SellAlert]) -> str:
    if not alerts:
        return "오늘 매도 검토 조건에 걸린 보유 종목이 없습니다."
    lines = ["[매도 검토 알림]"]
    for alert in alerts:
        lines.extend([
            f"{alert.name}({alert.ticker})",
            f"- 현재가: {alert.close:,}원",
            f"- 진입가: {alert.entry_price:,}원",
            f"- 수익률: {alert.return_pct:.1f}%",
            f"- 매도 검토 요약: {alert_summary(alert)}",
            f"- 사유: {alert.reason}",
        ])
    lines.append("조건 기반 매도 검토 알림이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def write_log(alerts: list[SellAlert], path: str = SELL_ALERTS_LOG) -> None:
    from .csv_schema import ensure_header, migrate_sell_alert_row

    header = ["created_at", "ticker", "name", "entry_price", "close", "return_pct", "summary", "reason"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ensure_header(path, header, migrate_sell_alert_row)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(header)
        for alert in alerts:
            writer.writerow([datetime.now().isoformat(timespec="seconds"), alert.ticker, alert.name, alert.entry_price, alert.close, f"{alert.return_pct:.2f}", alert_summary(alert), alert.reason])


def run() -> str:
    load_env()
    if not is_market_alert_time():
        return "market_closed"
    from .data_store import finish_run, start_run

    end_day = latest_naver_trading_day()
    run_id = start_run("sell_check", end_day.isoformat())
    try:
        alerts = find_alerts(read_positions(), end_day, run_id)
        write_log(alerts)
        finish_run(run_id)
    except Exception:
        finish_run(run_id, "failed")
        raise
    if not alerts and os.environ.get("SEND_EMPTY_SELL_ALERT", "0") != "1":
        return "no_alerts"
    from .notifier import send_notification

    return send_notification(format_message(alerts))


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
