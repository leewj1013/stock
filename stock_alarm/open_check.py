from __future__ import annotations

from datetime import date

from .daily_check import run_log_statuses, status


def lines(today: date | None = None) -> list[str]:
    return [status(today), *run_log_statuses(today, {"recommendations": "logs/recommendations.csv"})]


def main() -> int:
    result = lines()
    print("\n".join(result))
    return 0 if result[0].startswith("daily ok:") and result[1].endswith("=ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
