import csv
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

from stock_alarm.sell_check import SellAlert, active_positions, alert_summary, alerted_tickers, check_position, find_alerts, format_message, max_returns, previous_returns, read_positions, run, write_log


class SellCheckTest(unittest.TestCase):
    def test_read_positions(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            file.write("ticker,name,entry_price,entry_date\n005930,Samsung,80000,2026-07-25\n")
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        self.assertEqual("005930", read_positions(file.name)[0]["ticker"])

    def test_active_positions_keeps_only_the_latest_row_per_ticker(self):
        # data/positions.csv is append-only: track_positions() only ever adds
        # a new row for a ticker once its earlier entry is inactive, so a
        # stale old row (e.g. never pruned) must not be treated as a second,
        # independent position alongside the real current one.
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            file.write(
                "ticker,name,entry_price,entry_date\n"
                "000810,삼성화재,628000,2026-08-14\n"
                "000810,삼성화재,668000,2026-09-04\n"
            )
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        self.assertEqual(2, len(read_positions(file.name)), "read_positions() must still see every raw row")
        rows = active_positions(file.name)
        self.assertEqual(1, len(rows))
        self.assertEqual("668000", rows[0]["entry_price"])

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows")
    def test_check_position_alerts_on_stop_loss(self, naver_rows, _name):
        naver_rows.return_value = [[20260701, 0, 0, 0, 100, 1]] * 19 + [[20260724, 0, 0, 0, 94, 1]]
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24))
        self.assertEqual("005930", alert.ticker)
        self.assertIn("손절 기준", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 0, 0, 100, 1]] * 20)
    def test_check_position_does_not_alert_on_return_drop_alone(self, _rows, _name):
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), 5)
        self.assertIsNone(alert)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 0, 0, 102, 1]] * 20)
    def test_check_position_does_not_alert_on_profit_giveback_alone(self, _rows, _name):
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), max_return=8)
        self.assertIsNone(alert)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows")
    def test_check_position_confirms_return_drop_with_two_ma20_breaks(self, naver_rows, _name):
        naver_rows.return_value = [[20260701, 0, 0, 0, 110, 1]] * 19 + [[20260723, 0, 0, 0, 99, 1], [20260724, 0, 0, 0, 98, 1]]
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), 5)
        self.assertIn("20일선 2회 연속 이탈", alert.reason)
        self.assertIn("직전 평가 대비 수익률 7.0%p 악화", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 102, 98, 100, 1]] * 20)
    def test_check_position_applies_time_stop(self, _rows, _name):
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100", "entry_date": "2026-07-01"}, date(2026, 7, 24))
        self.assertIn("보유 후 기대수익 미달", alert.reason)

    @patch("stock_alarm.toss_client.blocking_warnings_for", return_value={"LIQUIDATION_TRADING"})
    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 0, 0, 110, 1]] * 20)
    def test_check_position_force_sells_on_a_serious_stock_warning(self, _rows, _name, _warnings):
        # A stock warning must override even a profitable, otherwise-quiet
        # position -- holding through liquidation trading is riskier than
        # any price-based reason to stay in.
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24))
        self.assertIn("종목 경고 발생: LIQUIDATION_TRADING", alert.reason)

    @patch("stock_alarm.toss_client.blocking_warnings_for")
    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 0, 0, 110, 1]] * 20)
    def test_check_position_does_not_call_toss_when_backtest_supplies_price_rows(self, naver_rows, _name, warnings):
        rows = [[20260701, 0, 0, 0, 110, 1]] * 20
        check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24), price_rows=rows)
        warnings.assert_not_called()

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows")
    def test_atr_can_widen_fixed_stop(self, naver_rows, _name):
        naver_rows.return_value = [[20260701, 0, 110, 90, 100, 1]] * 19 + [[20260724, 0, 110, 90, 94, 1]]
        alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, date(2026, 7, 24))
        self.assertIsNone(alert)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 112, 108, 110, 1]] * 20)
    def test_first_take_profit_is_partial(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "100"},
            date(2026, 7, 24), remaining_quantity=10,
        )
        self.assertEqual("partial", alert.sale_type)
        self.assertEqual("take_profit_1", alert.stage)
        self.assertEqual(0.5, alert.quantity_fraction)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 109, 107, 108, 1]] * 20)
    def test_profile_take_profit_threshold_and_ratio_are_applied(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "100"},
            date(2026, 7, 24), remaining_quantity=10,
            sell_policy={"take_profit_1_pct": 7.0, "take_profit_2_pct": 14.0, "take_profit_1_sell_ratio": 40.0},
        )
        self.assertEqual("take_profit_1", alert.stage)
        self.assertEqual(0.4, alert.quantity_fraction)
        self.assertIn("+7.0%", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 115, 113, 114, 1]] * 20)
    def test_profile_second_take_profit_threshold_is_applied(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "100"},
            date(2026, 7, 24), partial_taken=True, remaining_quantity=5,
            sell_policy={"take_profit_1_pct": 7.0, "take_profit_2_pct": 14.0},
        )
        self.assertEqual("take_profit_2", alert.stage)
        self.assertIn("+14.0%", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 101, 99, 100, 1]] * 19 + [[20260724, 0, 95, 93, 94, 1]])
    def test_stop_loss_still_closes_remainder_after_first_take_profit(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "100"},
            date(2026, 7, 24), partial_taken=True, remaining_quantity=5,
        )
        self.assertEqual("full", alert.sale_type)
        self.assertIn("손절 기준", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 115, 105, 110, 1]] * 19 + [[20260723, 0, 106, 98, 99, 1], [20260724, 0, 105, 97, 98, 1]])
    def test_ma20_break_closes_remainder_after_first_take_profit(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "90"},
            date(2026, 7, 24), partial_taken=True, remaining_quantity=5,
        )
        self.assertEqual("full", alert.sale_type)
        self.assertIn("20일선 2회 연속 이탈", alert.reason)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 122, 118, 120, 1]] * 20)
    def test_second_take_profit_closes_all_remainder(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "100"},
            date(2026, 7, 24), partial_taken=True, remaining_quantity=5,
        )
        self.assertEqual("full", alert.sale_type)
        self.assertEqual("take_profit_2", alert.stage)

    @patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
    @patch("stock_alarm.sell_check.naver_rows", return_value=[[20260701, 0, 122, 118, 120, 1]] * 20)
    def test_single_share_waits_until_second_target_then_sells_all(self, _rows, _name):
        alert = check_position(
            {"ticker": "005930", "name": "Samsung", "entry_price": "100"},
            date(2026, 7, 24), remaining_quantity=1,
        )
        self.assertEqual("full", alert.sale_type)
        self.assertEqual("take_profit_2", alert.stage)

    def test_returns_are_scoped_by_position_id(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["created_at", "position_id", "ticker", "return_pct"])
            writer.writerow(["now", "first", "005930", "8"])
            writer.writerow(["later", "first", "005930", "2"])
            writer.writerow(["later", "second", "005930", "10"])
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        self.assertEqual(8.0, max_returns(file.name)["first"])
        self.assertEqual(10.0, max_returns(file.name)["second"])
        self.assertEqual(2.0, previous_returns(file.name)["first"])

    def test_alerted_tickers(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["created_at", "ticker", "name"])
            writer.writerow(["2026-07-25", "005930", "Samsung"])
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        self.assertEqual({"005930"}, alerted_tickers(file.name))

    @patch("stock_alarm.sell_check.latest_sell_alert_times", return_value={"005930": datetime(2026, 7, 25)})
    @patch("stock_alarm.sell_check.check_position")
    def test_find_alerts_skips_position_sold_after_entry(self, check_position, _times):
        result = find_alerts([{"ticker": "005930", "name": "Samsung", "entry_price": "100", "entry_date": "2026-07-24"}], date(2026, 7, 24))
        self.assertEqual([], result)
        check_position.assert_not_called()

    @patch("stock_alarm.sell_check.latest_sell_alert_times", return_value={"005930": datetime(2026, 7, 25)})
    @patch("stock_alarm.sell_check.check_position")
    def test_find_alerts_checks_reentry_after_old_sell(self, check_position, _times):
        check_position.return_value = None
        find_alerts([{"ticker": "005930", "name": "Samsung", "entry_price": "100", "entry_date": "2026-07-26"}], date(2026, 7, 27))
        check_position.assert_called_once()

    def test_format_message_and_summary(self):
        alert = SellAlert("005930", "Samsung", 100, 94, -6.0, "손절 기준 -5.0% 이탈")
        message = format_message([alert])
        self.assertIn("매도 알림", message)
        self.assertIn("🔴 자동 매도", message)
        self.assertIn("수익률 -6.00%", message)
        self.assertIn("재추천 제한: 5일", message)
        self.assertEqual("고점 대비 수익 반납", alert_summary(SellAlert("A", "A", 100, 102, 2, "고점 대비 반납")))
        self.assertEqual("20일선 이탈", alert_summary(SellAlert("A", "A", 100, 101, 1, "20일선 이탈")))

    def test_format_message_shows_no_cooldown_for_partial_take_profit(self):
        alert = SellAlert("005930", "Samsung", 100, 110, 10.0, "1차 익절 목표 +10.0% 도달", sale_type="partial", stage="take_profit_1")

        message = format_message([alert])

        self.assertIn("재추천 제한: 없음(잔량 보유 중)", message)

    def test_format_message_includes_virtual_sale_execution(self):
        alert = SellAlert("005930", "Samsung", 100, 110, 10.0, "고점 대비 반납", 5)
        result = {"sold": 1, "cash": 1100, "executions": [{"ticker": "005930", "quantity": 10, "cost_basis": 1000, "realized_profit_loss": 100}]}
        message = format_message([alert], result)

        self.assertIn("🟠 수익 보호 매도", message)
        self.assertIn("보유 5일", message)
        self.assertIn("실현손익 +100원(+10.00%)", message)
        self.assertIn("매도 후 현금: 1,100원", message)

    def test_write_log(self):
        with tempfile.NamedTemporaryFile(delete=False) as file:
            path = file.name
        os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        write_log([SellAlert("005930", "Samsung", 100, 94, -6.0, "손절")], path)
        with open(path, newline="", encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual("손실 -6.0%", rows[0]["summary"])

    @patch("stock_alarm.data_store.virtual_sell", return_value={})
    @patch("stock_alarm.data_store.virtual_position_states", return_value={})
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    @patch("stock_alarm.sell_check.latest_naver_trading_day", return_value=date(2026, 7, 24))
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.sell_check.is_market_alert_time", return_value=True)
    @patch("stock_alarm.sell_check.find_alerts", return_value=[])
    @patch("stock_alarm.sell_check.read_positions", return_value=[])
    @patch("stock_alarm.sell_check.write_log")
    def test_run_skips_empty_alert_by_default(self, _write, _positions, _alerts, _trading, _start, _finish, _day, _state, _position_states, virtual_sell):
        self.assertEqual("no_alerts", run())
        # Both the aggressive and neutral profile call virtual_sell against
        # their own DB, even with an empty alert list.
        paths = {call.kwargs["path"] for call in virtual_sell.call_args_list}
        self.assertEqual({"data/stock_alarm.db", "data/stock_alarm_neutral.db"}, paths)

    @patch("stock_alarm.data_store.virtual_sell", return_value={})
    @patch("stock_alarm.data_store.virtual_position_states", return_value={})
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    @patch("stock_alarm.sell_check.latest_naver_trading_day", return_value=date(2026, 7, 24))
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.sell_check.is_market_alert_time", return_value=True)
    @patch("stock_alarm.sell_check.read_positions", return_value=[])
    @patch("stock_alarm.sell_check.write_log")
    def test_run_gives_neutral_profile_its_own_sell_policy_and_alert_log(self, _write, _positions, _trading, _start, _finish, _day, _state, _position_states, _sell):
        with patch("stock_alarm.sell_check.find_alerts", return_value=[]) as find_alerts_mock:
            run()
        # find_alerts(positions, end_day, run_id, virtual_states, quantities, sell_policy, alerted_log_path)
        calls = {call.args[6]: call.args[5] for call in find_alerts_mock.call_args_list}
        self.assertIsNone(calls["logs/sell_alerts.csv"])
        self.assertEqual({"stop_loss_pct": 3.0, "take_profit_1_pct": 7.0, "take_profit_2_pct": 14.0}, calls["logs/sell_alerts_neutral.csv"])

    @patch("stock_alarm.sell_check.is_market_alert_time", return_value=False)
    def test_run_skips_when_market_closed(self, _trading):
        self.assertEqual("market_closed", run())

    @patch("stock_alarm.sell_check.write_error_log")
    @patch("stock_alarm.data_store.virtual_sell", return_value={})
    @patch("stock_alarm.data_store.virtual_position_states", return_value={})
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    @patch("stock_alarm.sell_check.latest_naver_trading_day", return_value=date(2026, 7, 24))
    @patch("stock_alarm.data_store.finish_run")
    @patch("stock_alarm.data_store.start_run", return_value="test-run")
    @patch("stock_alarm.sell_check.is_market_alert_time", return_value=True)
    @patch("stock_alarm.sell_check.read_positions", return_value=[])
    @patch("stock_alarm.sell_check.write_log")
    def test_neutral_profile_failure_does_not_fail_the_primary_run(self, _write, _positions, _trading, start_run, finish_run, _day, _state, _position_states, _sell, write_error_log):
        # A bug in the comparison-only neutral profile must not mark the
        # aggressive account's own sell_check run as failed.
        def find_alerts_side_effect(*args, **kwargs):
            if args[6] == "logs/sell_alerts_neutral.csv":
                raise RuntimeError("boom")
            return []

        with patch("stock_alarm.sell_check.find_alerts", side_effect=find_alerts_side_effect):
            result = run()

        self.assertEqual("no_alerts", result)
        finish_run.assert_called_once_with("test-run")
        write_error_log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
