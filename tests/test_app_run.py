import unittest
from unittest.mock import patch

from stock_alarm.app import Pick, run
from stock_alarm.trading_profiles import PROFILES


class AppRunTest(unittest.TestCase):
    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "0"}, clear=True)
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.recommend_picks_by_profile", return_value={name: [] for name in PROFILES})
    @patch("stock_alarm.notifier.send_notification")
    def test_run_skips_empty_recommendation_by_default(self, send, _recommend, _write, _track, _env, _trading, _start, _finish):
        run()

        send.assert_not_called()

    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "0"}, clear=True)
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.recommend_picks_by_profile", return_value={"aggressive": [Pick("005930", "Samsung", 100, 2, 5_000_000_000, 60)], "neutral": []})
    @patch("stock_alarm.notifier.send_notification")
    def test_unbought_picks_are_held_for_the_briefing(self, send, _recommend, _write_log, _track_positions, _env, _trading, _start, _finish):
        # Sending every 5-minute batch averaged 16 alerts a day and buried the sell alerts.
        run()

        send.assert_not_called()

    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "0", "RECOMMENDATION_ALERTS": "all"}, clear=True)
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.recommend_picks_by_profile", return_value={"aggressive": [Pick("005930", "Samsung", 100, 2, 5_000_000_000, 60)], "neutral": []})
    @patch("stock_alarm.notifier.send_notification")
    def test_all_mode_still_sends_every_batch(self, send, _recommend, _write_log, _track_positions, _env, _trading, _start, _finish):
        run()

        send.assert_called_once()

    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "1"}, clear=True)
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.screener.fundamentals_for", return_value={})
    @patch("stock_alarm.app.auto_buy_virtual_trader")
    @patch("stock_alarm.app.recommend_picks_by_profile", return_value={"aggressive": [Pick("005930", "Samsung", 100, 2, 5_000_000_000, 60)], "neutral": []})
    @patch("stock_alarm.notifier.send_notification")
    def test_bought_picks_are_sent_right_away(self, send, _recommend, auto_buy, _fundamentals, _write_log, _track_positions, _env, _trading, _start, _finish):
        auto_buy.return_value = {"spent": 100, "cash": 900, "executions": [{"ticker": "005930", "price": 100, "quantity": 1, "cost": 100}]}

        run()

        send.assert_called_once()
        self.assertIn("[가상매수 체결", send.call_args.args[0])

    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "0"}, clear=True)
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.recommend_picks_by_profile", return_value={"aggressive": [Pick("005930", "Samsung", 100, 2, 5_000_000_000, 60)], "neutral": []})
    @patch("stock_alarm.notifier.send_notification")
    def test_run_tracks_positions_before_writing_recommendation_log(self, _send, _recommend, write_log, track_positions, _env, _trading, _start, _finish):
        calls = []
        track_positions.side_effect = lambda *_args, **_kwargs: calls.append("track")
        write_log.side_effect = lambda *_args, **_kwargs: calls.append("log")

        run()

        self.assertEqual(["track", "log"], calls)

    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "1"}, clear=True)
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.app.is_market_alert_time", return_value=True)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.track_positions")
    @patch("stock_alarm.app.write_log")
    @patch("stock_alarm.app.auto_buy_virtual_trader")
    @patch("stock_alarm.app.recommend_picks_by_profile")
    @patch("stock_alarm.notifier.send_notification")
    def test_run_buys_each_profile_with_its_own_pick_list(self, _send, recommend, auto_buy, _write_log, _track_positions, _env, _trading, _start, _finish):
        aggressive_pick = Pick("005930", "Samsung", 100, 2, 5_000_000_000, 60)
        neutral_pick = Pick("000660", "SK hynix", 200, 2, 5_000_000_000, 55)
        recommend.return_value = {"aggressive": [aggressive_pick], "neutral": [neutral_pick]}

        run()

        buys_by_path = {call.kwargs.get("path", "data/stock_alarm.db"): call.args[0] for call in auto_buy.call_args_list}
        self.assertEqual([aggressive_pick], buys_by_path["data/stock_alarm.db"])
        self.assertEqual([neutral_pick], buys_by_path[PROFILES["neutral"]["db_path"]])

    @patch.dict("os.environ", {"VIRTUAL_TRADER_AUTO_BUY": "0"}, clear=True)
    @patch("stock_alarm.app.is_market_alert_time", return_value=False)
    @patch("stock_alarm.app.load_env")
    @patch("stock_alarm.app.recommend_picks_by_profile")
    @patch("stock_alarm.notifier.send_notification")
    def test_run_skips_when_market_closed(self, send, recommend, _env, _trading):
        run()

        recommend.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
