from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from .app import configured_stocks, is_trading_day, latest_naver_trading_day, load_env, naver_rows, stock_name, write_error_log
from .notifier import send_notification


def market_rows(end_day: date | None = None) -> list[dict[str, str]]:
    day = end_day or latest_naver_trading_day()
    rows = []
    for ticker, fallback in configured_stocks().items():
        prices = naver_rows(ticker, day - timedelta(days=10), day)
        if len(prices) < 2:
            continue
        previous = int(prices[-2][4])
        close = int(prices[-1][4])
        volume = int(prices[-1][5])
        change = (close - previous) / previous * 100 if previous else 0
        rows.append(
            {
                "ticker": ticker,
                "name": stock_name(ticker, fallback),
                "change_pct": f"{change:.2f}",
                "trading_value": str(close * volume),
            }
        )
    return rows


def summary(rows: list[dict[str, str]]) -> dict[str, str]:
    changes = [float(row["change_pct"]) for row in rows]
    up_count = sum(value > 0 for value in changes)
    down_count = sum(value < 0 for value in changes)
    return {
        "count": str(len(rows)),
        "up_count": str(up_count),
        "down_count": str(down_count),
        "up_ratio_pct": f"{up_count / len(rows) * 100:.1f}" if rows else "0.0",
        "avg_change_pct": f"{mean(changes):.2f}" if changes else "0.00",
    }


def message(rows: list[dict[str, str]] | None = None) -> str:
    rows = rows if rows is not None else market_rows()
    info = summary(rows)
    leaders = sorted(rows, key=lambda row: int(row.get("trading_value") or 0), reverse=True)[:3]
    lines = [
        "[아침 시황 요약]",
        f"기준: 관심종목 {info['count']}개",
        f"상승/하락: {info['up_count']}개 / {info['down_count']}개",
        f"상승 비율: {info['up_ratio_pct']}%",
        f"평균 등락률: {info['avg_change_pct']}%",
    ]
    if leaders:
        lines.append("거래대금 상위:")
        lines.extend(f"- {row['name']}({row['ticker']}): {row['change_pct']}%" for row in leaders)
    lines.append("별도 시황 API 키 없이 네이버 종목 시세 기준으로 계산했습니다.")
    return "\n".join(lines)


def run() -> str:
    load_env()
    if not is_trading_day():
        return "market_closed"
    return send_notification(message())


def main() -> None:
    try:
        print(run())
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
