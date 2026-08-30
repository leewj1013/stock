import os
import unittest
from unittest.mock import patch

from stock_alarm.virtual_trader_report import run, toss_reference_price


class VirtualTraderReportTest(unittest.TestCase):
    def _state(self):
        return {"holdings": [], "total_equity": 100_000, "holdings_value": 0}

    @patch("stock_alarm.notifier.send_notification")
    @patch("stock_alarm.virtual_trader_report.risk_snapshot", return_value={"transition": "halted", "reason": "daily_loss_limit"})
    @patch("stock_alarm.virtual_trader_report.record_virtual_valuation", return_value={"equity": 100_000, "return_pct": -2, "return_change_pct": -2})
    @patch("stock_alarm.virtual_trader_report.virtual_trader_state")
    @patch("stock_alarm.virtual_trader_report.current_prices", return_value={})
    def test_halt_message_confirms_holdings_are_kept_and_sells_continue(self, _prices, state, _record, _risk, send):
        state.return_value = self._state()
        run()
        message = send.call_args.args[0]
        self.assertIn("보유종목은 유지", message)
        self.assertIn("분할익절과 개별 매도조건은 계속 감시", message)

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


if __name__ == "__main__":
    unittest.main()
