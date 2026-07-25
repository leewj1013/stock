import os
import unittest
from unittest.mock import patch

from stock_alarm.notifier import send_notification


class NotifierTest(unittest.TestCase):
    def test_missing_telegram_config_falls_back_to_console(self):
        old_notifier = os.environ.get("NOTIFIER")
        old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        old_chat = os.environ.get("TELEGRAM_CHAT_ID")
        os.environ["NOTIFIER"] = "telegram"
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"] = ""
        try:
            with patch("stock_alarm.notifier.send_console") as console:
                with patch("stock_alarm.notifier.was_sent", return_value=False):
                    with patch("stock_alarm.notifier.mark_sent"):
                        self.assertEqual("console", send_notification("hello"))
                        console.assert_called_once_with("hello")
        finally:
            for key, value in {
                "NOTIFIER": old_notifier,
                "TELEGRAM_BOT_TOKEN": old_token,
                "TELEGRAM_CHAT_ID": old_chat,
            }.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
