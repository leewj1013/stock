import os
import unittest

from stock_alarm.notifier import telegram_get_me


class TelegramCheckTest(unittest.TestCase):
    def test_requires_token(self):
        old = os.environ.get("TELEGRAM_BOT_TOKEN")
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        try:
            with self.assertRaises(RuntimeError):
                telegram_get_me()
        finally:
            if old is None:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            else:
                os.environ["TELEGRAM_BOT_TOKEN"] = old


if __name__ == "__main__":
    unittest.main()
