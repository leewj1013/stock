import unittest
from unittest.mock import patch

from stock_alarm.dashboard_server import allowed_origin, is_local_host, prices, remote_setup_page, valid_remote_token


class DashboardServerTest(unittest.TestCase):
    def test_remote_security_helpers(self):
        self.assertTrue(is_local_host("127.0.0.1:8765"))
        self.assertFalse(is_local_host("stock-api.example.com"))
        self.assertEqual("https://leewj1013.github.io", allowed_origin("https://leewj1013.github.io"))
        self.assertEqual("", allowed_origin("https://evil.example"))
        with patch.dict("os.environ", {"DASHBOARD_REMOTE_TOKEN": "secret"}):
            self.assertTrue(valid_remote_token("Bearer secret"))
            self.assertFalse(valid_remote_token("Bearer wrong"))

    @patch("stock_alarm.dashboard_server.os.environ", {"DASHBOARD_REMOTE_TOKEN": "secret"})
    @patch("builtins.open", side_effect=OSError)
    def test_remote_setup_keeps_token_off_remote_url(self, _open):
        page = remote_setup_page()
        self.assertIn('value=&quot;secret&quot;', page)
        self.assertNotIn("token=secret", page)
    @patch("stock_alarm.dashboard_server.naver_rows", return_value=[["20260826", 0, 0, 0, 204500, 1]])
    @patch("stock_alarm.dashboard_server.virtual_trader_state", return_value={"cash": 0, "holdings": [{"ticker": "086280"}]})
    @patch("stock_alarm.dashboard_server.recommendations", return_value=[])
    @patch("stock_alarm.dashboard_server.latest_position_rows", return_value=[{"ticker": "086280", "close": "206000"}])
    def test_prices_refreshes_virtual_holding_instead_of_using_stale_report(self, _positions, _recommendations, _state, _naver):
        self.assertEqual(204500, prices()["086280"])


if __name__ == "__main__":
    unittest.main()
