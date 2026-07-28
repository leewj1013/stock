from __future__ import annotations

from .app import is_trading_day, load_env, write_error_log
from .notifier import send_notification
from .positions_check import position_count
from .positions_report import change_summary
from .report import tail_csv


def latest_position_summary() -> str:
    rows = tail_csv("logs/positions_report.csv", 50)
    if not rows:
        return "보유 수익률 기록 없음"
    latest_time = rows[-1].get("created_at")
    latest = [row for row in rows if row.get("created_at") == latest_time]
    returns = [float(row.get("return_pct") or 0) for row in latest]
    if not returns:
        return "보유 수익률 기록 없음"
    return f"보유 평균 수익률: {sum(returns) / len(returns):+.2f}%"


def top_recommendations(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["추천 TOP3: 없음"]
    lines = ["추천 TOP3:"]
    for index, row in enumerate(rows[:3], 1):
        name = row.get("name") or row.get("ticker") or "?"
        ticker = row.get("ticker") or "?"
        lines.append(f"{index}. {name}({ticker})")
    return lines


def message() -> str:
    recommendations = tail_csv("logs/recommendations.csv", 5)
    sell_alerts = tail_csv("logs/sell_alerts.csv", 5)
    lines = [
        "[오늘의 주식 알림 요약]",
        f"추천 후보: {len(recommendations)}개",
        f"매도 검토: {len(sell_alerts)}개" if sell_alerts else "매도 검토: 없음",
        f"보유 종목: {position_count()}개",
        latest_position_summary(),
        change_summary().replace("change=", "직전 대비: ").replace(" since previous", ""),
    ]
    lines.extend(top_recommendations(recommendations))
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
