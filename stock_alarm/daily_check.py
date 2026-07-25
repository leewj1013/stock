from __future__ import annotations

import os
from datetime import date

from .notifier import send_notification
from .report import latest_error_summary, tail_csv, tail_text


OK_CHANNELS = {"telegram", "skipped_duplicate"}
RUN_LOGS = {
    "recommendations": "logs/recommendations.csv",
    "positions_report": "logs/positions_report.csv",
    "recommendation_performance": "logs/recommendation_performance.csv",
}


def status(today: date | None = None, path: str = "logs/deliveries.csv") -> str:
    day = (today or date.today()).isoformat()
    rows = [row for row in tail_csv(path, 200) if row.get("created_at", "").startswith(day)]
    ok = [row for row in rows if row.get("channel") in OK_CHANNELS]
    if ok:
        last = ok[-1]
        return f"daily ok: {last.get('channel')} at {last.get('created_at')}"
    if rows:
        last = rows[-1]
        return f"daily not-ok: last={last.get('channel')} at {last.get('created_at')}"
    return "daily not-ok: no delivery today"


def lines(today: date | None = None, path: str = "logs/deliveries.csv") -> list[str]:
    deliveries = tail_csv(path, 200)
    return [status(today, path), *run_log_statuses(today), task_error_status(today=today), f"latest_error={latest_error_summary(deliveries)}"]


def task_error_status(path: str = "logs/task.err.log", today: date | None = None) -> str:
    if not tail_text(path, 1):
        return "task_error=none"
    modified = date.fromtimestamp(os.path.getmtime(path))
    return f"task_error={'found' if modified == (today or date.today()) else 'old'}"


def run_log_statuses(today: date | None = None, paths: dict[str, str] | None = None) -> list[str]:
    day = (today or date.today()).isoformat()
    result = []
    for name, path in (paths or RUN_LOGS).items():
        rows = tail_csv(path, 500)
        ok = bool(rows) if name == "recommendation_performance" else any((row.get("created_at") or row.get("pick_date") or "").startswith(day) for row in rows)
        result.append(f"{name}={'ok' if ok else 'missing'}")
    return result


def main() -> int:
    result = lines()
    message = "\n".join(result)
    print(message)
    if os.environ.get("SEND_DAILY_CHECK_ALERT", "0") == "1":
        send_notification(message)
    return 0 if result[0].startswith("daily ok:") else 1


if __name__ == "__main__":
    raise SystemExit(main())
