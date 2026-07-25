from __future__ import annotations

import os

from .app import load_env, write_error_log
from .notifier import send_notification


def main() -> None:
    try:
        load_env()
        send_notification(os.environ.get("TELEGRAM_TEST_MESSAGE", "stockAlarm Telegram test"))
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
