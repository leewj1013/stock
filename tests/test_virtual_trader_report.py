import os
import unittest
from datetime import datetime as real_datetime
from unittest.mock import patch

from stock_alarm.virtual_trader_report import current_prices, risk_reason_lines, run, toss_reference_price


class VirtualTraderReportTest(unittest.TestCase):
    def _state(self):
        return {"holdings": [], "total_equity": 100_000, "holdings_value": 0}

    @patch("stock_alarm.notifier.send_notification")
    @patch("stock_alarm.virtual_trader_report.risk_snapshot", return_value={"transition": "halted", "reason": "daily_loss_limit", "daily_return_pct": -2.35})
    @patch("stock_alarm.virtual_trader_report.record_virtual_valuation", return_value={"equity": 100_000, "return_pct": -2, "return_change_pct": -2})
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state")
    @patch("stock_alarm.virtual_trader_report.current_prices", return_value={})
    def test_halt_message_confirms_holdings_are_kept_and_sells_continue(self, _prices, state, _record, _risk, send):
        state.return_value = self._state()
        run()
        message = send.call_args.args[0]
        self.assertIn("보유종목은 유지", message)
        self.assertIn("분할익절과 개별 매도조건은 계속 감시", message)
        self.assertIn("당일 계좌 손실 한도 도달", message)
        self.assertIn("당일 손익률 -2.35%", message)
        self.assertNotIn("daily_loss_limit", message)

    def test_risk_reason_lines_translate_multiple_internal_codes(self):
        lines = risk_reason_lines({
            "reason": "weekly_loss_limit,drawdown_limit,exposure_limit",
            "weekly_return_pct": -5.4,
            "drawdown_pct": -10.2,
            "exposure_pct": 72.5,
        })

        message = "\n".join(lines)
        self.assertIn("주간 계좌 손실 한도 도달", message)
        self.assertIn("계좌 최고점 대비 최대낙폭 한도 도달", message)
        self.assertIn("보유종목 투자비중 한도 초과", message)
        self.assertNotIn("weekly_loss_limit", message)

    @patch("stock_alarm.notifier.send_notification")
    @patch("stock_alarm.virtual_trader_report.risk_snapshot", return_value={"transition": "halted", "reason": "daily_loss_limit", "daily_return_pct": -2.35})
    @patch("stock_alarm.virtual_trader_report.record_virtual_valuation", return_value={"equity": 100_000, "return_pct": -2, "return_change_pct": -2})
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state")
    @patch("stock_alarm.virtual_trader_report.current_prices", return_value={})
    def test_secondary_profile_halt_does_not_notify_twice(self, _prices, state, _record, risk_snapshot, send):
        # Both the aggressive and neutral profiles run through the same halt
        # check, but only the aggressive (notify=True) profile should send a
        # Telegram message -- the neutral profile is comparison-only.
        state.return_value = self._state()
        run()
        send.assert_called_once()
        self.assertEqual(2, risk_snapshot.call_count)
        paths = {call.kwargs.get("path") for call in risk_snapshot.call_args_list}
        self.assertEqual({"data/stock_alarm.db", "data/stock_alarm_neutral.db"}, paths)

    @patch("stock_alarm.notifier.send_notification")
    @patch("stock_alarm.virtual_trader_report.risk_snapshot", return_value={"transition": "resumed", "reason": ""})
    @patch("stock_alarm.virtual_trader_report.record_virtual_valuation", return_value={"equity": 100_000, "return_pct": 0, "return_change_pct": 0})
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state")
    @patch("stock_alarm.virtual_trader_report.current_prices", return_value={})
    def test_resume_message_confirms_individual_sell_monitoring(self, _prices, state, _record, _risk, send):
        state.return_value = self._state()
        run()
        message = send.call_args.args[0]
        self.assertIn("위험중단 해제", message)
        self.assertIn("보유종목은 유지", message)

    @patch.dict(os.environ, {}, clear=True)
    def test_toss_reference_price_without_credentials_returns_none(self):
        self.assertIsNone(toss_reference_price("005930"))

    @patch.dict(os.environ, {"TOSS_CLIENT_ID": "id", "TOSS_CLIENT_SECRET": "secret"})
    @patch("stock_alarm.toss_client.TossClient.prices", return_value=[{"lastPrice": "256500"}])
    @patch("stock_alarm.toss_client.TossClient.access_token", return_value="token")
    def test_toss_reference_price_parses_last_price(self, _token, _prices):
        self.assertEqual(256500, toss_reference_price("005930"))

    @patch.dict(os.environ, {"TOSS_CLIENT_ID": "id", "TOSS_CLIENT_SECRET": "secret"})
    @patch("stock_alarm.toss_client.TossClient.prices", side_effect=RuntimeError("network"))
    @patch("stock_alarm.toss_client.TossClient.access_token", return_value="token")
    def test_toss_reference_price_swallows_errors(self, _token, _prices):
        self.assertIsNone(toss_reference_price("005930"))

    @patch("stock_alarm.virtual_trader_report.checked_prices", return_value=({}, []))
    @patch("stock_alarm.virtual_trader_report.datetime")
    @patch("stock_alarm.virtual_trader_report.toss_reference_price", return_value=207000)
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state", return_value={"holdings": [{"ticker": "086280"}]})
    def test_reference_close_uses_toss_during_regular_session(self, _state, toss, datetime_mock, checked_prices):
        datetime_mock.now.return_value = real_datetime(2026, 9, 1, 15, 0)
        datetime_mock.strptime = real_datetime.strptime

        current_prices()

        reference_provider = checked_prices.call_args.kwargs["reference_provider"]
        self.assertEqual(207000, reference_provider("086280"))
        toss.assert_called_with("086280")

    @patch("stock_alarm.virtual_trader_report.checked_prices", return_value=({}, []))
    @patch("stock_alarm.virtual_trader_report.datetime")
    @patch("stock_alarm.virtual_trader_report.toss_reference_price", return_value=207000)
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state", return_value={"holdings": []})
    def test_current_prices_records_quality_checks_under_the_requested_profiles_db(self, _state, _toss, datetime_mock, checked_prices):
        # A secondary profile's price-quality checks must land in its own DB,
        # not silently fall back to the default (aggressive) one.
        datetime_mock.now.return_value = real_datetime(2026, 9, 1, 10, 0)
        datetime_mock.strptime = real_datetime.strptime

        current_prices(path="data/stock_alarm_neutral.db")

        self.assertEqual("data/stock_alarm_neutral.db", checked_prices.call_args.kwargs["path"])

    @patch("pykrx.stock.get_market_ohlcv_by_date")
    @patch("stock_alarm.virtual_trader_report.checked_prices", return_value=({}, []))
    @patch("stock_alarm.virtual_trader_report.datetime")
    @patch("stock_alarm.virtual_trader_report.toss_reference_price", return_value=207000)
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state", return_value={"holdings": [{"ticker": "086280"}]})
    def test_reference_close_skips_toss_once_after_hours_session_starts(self, _state, toss, datetime_mock, checked_prices, _pykrx):
        # 16:00 is when KRX's 시간외단일가 session opens; Toss's live price can
        # legitimately diverge from the regular session's close from then on,
        # so it must stop being used as a "should match close" reference.
        datetime_mock.now.return_value = real_datetime(2026, 9, 1, 16, 0)
        datetime_mock.strptime = real_datetime.strptime

        current_prices()

        reference_provider = checked_prices.call_args.kwargs["reference_provider"]
        reference_provider("086280")
        toss.assert_not_called()


if __name__ == "__main__":
    unittest.main()
