import unittest
from unittest.mock import patch

from stock_alarm.app import Pick, run


class AppRunTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.recommend", return_value=[])
    @patch("stock_alarm.notifier.send_notification")
    def test_run_skips_empty_recommendation_by_default(self, send, _recommend, _write, _track, _env, _trading):
        run()

        send.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.recommend", return_value=[Pick("005930", "Samsung", 100, 2, 5_000_000_000, 60)])
    @patch("stock_alarm.notifier.send_notification")
    def test_run_sends_when_pick_exists(self, send, _recommend, _write, _track, _env, _trading):
        run()

        send.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    @patch("stock_alarm.app.is_market_alert_time", return_value=False)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.recommend")
    @patch("stock_alarm.notifier.send_notification")
    def test_run_skips_when_market_closed(self, send, recommend, _env, _trading):
        run()

        recommend.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
