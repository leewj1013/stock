import os
import json
import urllib.parse
import unittest
from urllib.error import URLError
from unittest.mock import patch

from stock_alarm.notifier import send_notification, send_telegram, split_telegram_message


class NotifierTest(unittest.TestCase):
    def test_split_telegram_message_keeps_every_chunk_under_safe_limit(self):
        message = ("한글 시황 문장\n" * 800) + ("x" * 4000)
        chunks = split_telegram_message(message)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3500 for chunk in chunks))
        self.assertEqual(message.replace("\n", ""), "".join(chunks).replace("\n", ""))

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "1234"}, clear=True)
    @patch("stock_alarm.notifier.urllib.request.urlopen")
    def test_send_telegram_sends_long_message_in_multiple_parts(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.side_effect = [
            json.dumps({"ok": True, "result": {"message_id": index, "chat": {"id": 1234}}}).encode()
            for index in range(1, 10)
        ]
        receipt = send_telegram("내용\n" * 2000)

        self.assertGreater(urlopen.call_count, 1)
        for call in urlopen.call_args_list:
            request = call.args[0]
            text = urllib.parse.parse_qs(request.data.decode())["text"][0]
            self.assertLessEqual(len(text), 4096)
        self.assertIn("|", receipt["message_id"])

    def test_missing_telegram_config_falls_back_to_console(self):
        old_notifier = os.environ.get("NOTIFIER")
        old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        old_chat = os.environ.get("TELEGRAM_CHAT_ID")
        os.environ["NOTIFIER"] = "telegram"
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
        os.environ["TELEGRAM_CHAT_ID"] = ""
        try:
            with patch("stock_alarm.notifier.write_delivery_log") as delivery:
                with patch("stock_alarm.notifier.send_console") as console:
                    with patch("stock_alarm.notifier.was_sent", return_value=False):
                        with patch("stock_alarm.notifier.mark_sent"):
                            self.assertEqual("console", send_notification("hello", event_type="recommendation"))
                            console.assert_called_once_with("hello")
                            delivery.assert_called_once()
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

    @patch.dict(os.environ, {"NOTIFIER": "telegram", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1"}, clear=True)
    @patch("stock_alarm.notifier.write_delivery_log")
    @patch("stock_alarm.notifier.send_console")
    @patch("stock_alarm.notifier.was_sent", return_value=False)
    @patch("stock_alarm.notifier.send_telegram", side_effect=URLError("network"))
    def test_telegram_error_falls_back_without_crashing(self, _telegram, _sent, console, delivery):
        self.assertEqual("console", send_notification("hello", event_type="recommendation"))

        console.assert_called_once_with("hello")
        delivery.assert_called_once()
        self.assertEqual("console", delivery.call_args.args[0])
        self.assertEqual("fallback", delivery.call_args.kwargs["status"])
        self.assertIn("URLError", delivery.call_args.kwargs["error"])

    @patch.dict(os.environ, {"NOTIFIER": "telegram", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1234"}, clear=True)
    @patch("stock_alarm.notifier.write_delivery_log")
    @patch("stock_alarm.notifier.was_sent", return_value=False)
    @patch("stock_alarm.notifier.mark_sent")
    @patch("stock_alarm.notifier.send_telegram", return_value={"message_id": "77", "chat_id_suffix": "1234"})
    def test_telegram_success_records_receipt(self, _telegram, _mark, _sent, delivery):
        self.assertEqual("telegram", send_notification("hello", event_type="recommendation"))

        delivery.assert_called_once_with(
            "telegram",
            status="delivered",
            message_id="77",
            chat_id_suffix="1234",
            event_type="recommendation",
            tickers=[],
        )

    @patch.dict(os.environ, {"FORCE_SEND": "1"})
    @patch("stock_alarm.notifier.write_delivery_log")
    @patch("stock_alarm.notifier.was_sent")
    @patch("stock_alarm.notifier.send_telegram")
    @patch("stock_alarm.notifier.send_kakao")
    def test_other_events_are_suppressed_even_when_forced(self, kakao, telegram, sent, delivery):
        for event in ("", "strategy_change", "daily_review", "portfolio_risk_halt", "portfolio_risk_resume", "us_account"):
            with self.subTest(event=event):
                self.assertEqual("suppressed", send_notification("internal status", event_type=event))
        kakao.assert_not_called()
        telegram.assert_not_called()
        sent.assert_not_called()
        self.assertEqual(6, delivery.call_count)

    @patch.dict(os.environ, {"NOTIFIER": "telegram"})
    @patch("stock_alarm.notifier.write_delivery_log")
    @patch("stock_alarm.notifier.was_sent", return_value=False)
    @patch("stock_alarm.notifier.mark_sent")
    @patch("stock_alarm.notifier.send_telegram", return_value=True)
    def test_requested_events_remain_enabled(self, telegram, mark, sent, delivery):
        for event in ("market_summary", "recommendation", "sell", "sell_reminder", "open_orders", "daily_summary"):
            with self.subTest(event=event):
                self.assertEqual("telegram", send_notification("message", event_type=event))
        self.assertEqual(6, telegram.call_count)


if __name__ == "__main__":
    unittest.main()
