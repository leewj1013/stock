from __future__ import annotations

import sys

from .app import load_env, write_error_log
from .notifier import send_notification


def message(step: str, exit_code: str) -> str:
    return f"[stockAlarm 장애 알림]\n실패 단계: {step}\n종료 코드: {exit_code}\nlogs/task.err.log를 확인하세요."


def run(step: str, exit_code: str) -> str:
    load_env()
    return send_notification(message(step, exit_code))


def main() -> None:
    try:
        run(sys.argv[1] if len(sys.argv) > 1 else "unknown", sys.argv[2] if len(sys.argv) > 2 else "unknown")
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
