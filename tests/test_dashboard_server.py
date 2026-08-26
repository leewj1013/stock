import unittest
from unittest.mock import patch

from stock_alarm.dashboard_server import prices


class DashboardServerTest(unittest.TestCase):
    @patch("stock_alarm.dashboard_server.naver_rows", return_value=[["20260826", 0, 0, 0, 204500, 1]])
    @patch("stock_alarm.dashboard_server.virtual_trader_state", return_value={"cash": 0, "holdings": [{"ticker": "086280"}]})
    @patch("stock_alarm.dashboard_server.recommendations", return_value=[])
    @patch("stock_alarm.dashboard_server.latest_position_rows", return_value=[{"ticker": "086280", "close": "206000"}])
    def test_prices_refreshes_virtual_holding_instead_of_using_stale_report(self, _positions, _recommendations, _state, _naver):
        self.assertEqual(204500, prices()["086280"])


if __name__ == "__main__":
    unittest.main()
