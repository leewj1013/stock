from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from .app import env_float, latest_naver_trading_day, load_env, naver_rows, stock_name, write_error_log


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
) -> SellAlert | None:
    ticker = position["ticker"].strip()
    entry_price = int(float(position["entry_price"]))
    rows = naver_rows(ticker, end_day - timedelta(days=90), end_day)
    if len(rows) < 20 or entry_price <= 0:
        return None

    closes = [int(row[4]) for row in rows[-20:]]
    close = closes[-1]
    ma20 = mean(closes)
    return_pct = (close - entry_price) / entry_price * 100
    stop_loss_pct = -abs(env_float("SELL_LOSS_PCT", 5))

    reasons = []
    if return_pct <= stop_loss_pct:
        reasons.append(f"손절 기준 {stop_loss_pct:.1f}% 이탈")
    if close < ma20:
        reasons.append("20일선 이탈")
    if previous_return is not None:
        drop = previous_return - return_pct
        if drop >= env_float("SELL_DROP_PCT", 3):
            reasons.append(f"직전 점검 대비 수익률 {drop:.1f}%p 악화")
    if max_return is not None and max_return >= env_float("SELL_PROTECT_PROFIT_PCT", 5):
        giveback = max_return - return_pct
        if giveback >= env_float("SELL_GIVEBACK_PCT", 4):
            reasons.append(f"profit giveback from {max_return:.1f}% by {giveback:.1f}%p")
    if not reasons:
        return None

    name = stock_name(ticker, position.get("name", ticker).strip() or ticker)
    return SellAlert(ticker, name, entry_price, close, return_pct, ", ".join(reasons))


def previous_returns(path: str = POSITIONS_REPORT_LOG) -> dict[str, float]:
    if not os.path.exists(path):
        return {}
    latest: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if row.get("ticker"):
                latest[row["ticker"]] = float(row.get("return_pct") or 0)
    return latest


def max_returns(path: str = POSITIONS_REPORT_LOG) -> dict[str, float]:
    if not os.path.exists(path):
        return {}
    result: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            ticker = row.get("ticker")
            if ticker:
                result[ticker] = max(result.get(ticker, float("-inf")), float(row.get("return_pct") or 0))
    return result


def find_alerts(positions: list[dict[str, str]], end_day: date) -> list[SellAlert]:
    previous = previous_returns()
    best = max_returns()
    alerts = []
    for position in positions:
        ticker = position["ticker"].strip()
        alert = check_position(position, end_day, previous.get(ticker), best.get(ticker))
        if alert:
            alerts.append(alert)
    return alerts


def format_message(alerts: list[SellAlert]) -> str:
    if not alerts:
        return "오늘 매도 검토 조건에 걸린 보유 종목이 없습니다."
    lines = ["[매도 검토 알림]"]
    for alert in alerts:
        lines.append(f"{alert.name}({alert.ticker})")
        lines.append(f"- 현재가: {alert.close:,}원")
        lines.append(f"- 진입가: {alert.entry_price:,}원")
        lines.append(f"- 손익률: {alert.return_pct:.1f}%")
        lines.append(f"- 매도 경고 요약: {alert_summary(alert)}")
        lines.append(f"- 사유: {alert.reason}")
    lines.append("※ 조건 기반 매도 검토 알림이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def alert_summary(alert: SellAlert) -> str:
    if "profit giveback" in alert.reason:
        return "고점 대비 수익 반납"
    if alert.return_pct <= -abs(env_float("SELL_LOSS_PCT", 5)):
        return f"손실 {alert.return_pct:.1f}%"
    if "20" in alert.reason:
        return "20일선 이탈"
    return "수익률 악화"


def write_log(alerts: list[SellAlert], path: str = SELL_ALERTS_LOG) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(["created_at", "ticker", "name", "entry_price", "close", "return_pct", "summary", "reason"])
        for alert in alerts:
            writer.writerow(
                [
                    datetime.now().isoformat(timespec="seconds"),
                    alert.ticker,
                    alert.name,
                    alert.entry_price,
                    alert.close,
                    f"{alert.return_pct:.2f}",
                    alert_summary(alert),
                    alert.reason,
                ]
            )


def run() -> str:
    load_env()
    alerts = find_alerts(read_positions(), latest_naver_trading_day())
    write_log(alerts)
    if not alerts and os.environ.get("SEND_EMPTY_SELL_ALERT", "0") != "1":
        return "no_alerts"
    message = format_message(alerts)
    from .notifier import send_notification

    return send_notification(message)


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
