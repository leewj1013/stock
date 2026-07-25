from __future__ import annotations

from .app import load_env, write_error_log
from .notifier import telegram_get_me


def main() -> None:
    try:
        load_env()
        body = telegram_get_me()
        result = body.get("result", {})
        print(f"ok={body.get('ok')}")
        print(f"bot_id={result.get('id')}")
        print(f"bot_username={result.get('username')}")
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
