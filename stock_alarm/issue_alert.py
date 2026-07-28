from __future__ import annotations

from .app import is_trading_day, load_env, write_error_log
from .dashboard import issue_rows
from .notifier import send_notification


def message(rows: list[dict[str, str]]) -> str:
    if not rows or rows == [{"source": "dashboard", "item": "issues", "status": "none"}]:
        return ""
    lines = ["[stockAlarm 운영 이슈]"]
    lines.extend(f"- {row.get('source')} {row.get('item')}: {row.get('status')}" for row in rows)
    return "\n".join(lines)


def run() -> str:
    load_env()
    if not is_trading_day():
        return "market_closed"
    text = message(issue_rows())
    if not text:
        return "no_issues"
    try:
        return send_notification(text)
    except Exception as error:
        write_error_log(error)
        return "issue_alert_failed"


def main() -> None:
    try:
        print(run())
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
