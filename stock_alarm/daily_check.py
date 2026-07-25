from __future__ import annotations

from datetime import date

from .report import latest_error_summary, tail_csv


OK_CHANNELS = {"telegram", "skipped_duplicate"}


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
    return [status(today, path), f"latest_error={latest_error_summary(deliveries)}"]


def main() -> int:
    result = lines()
    print("\n".join(result))
    return 0 if result[0].startswith("daily ok:") else 1


if __name__ == "__main__":
    raise SystemExit(main())
