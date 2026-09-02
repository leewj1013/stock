import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from stock_alarm.dashboard_server import RemoteReadOnlyHandler, allowed_origin, prices, profile_db_path, remote_setup_page, trader_payload, valid_remote_token


class DashboardServerTest(unittest.TestCase):
    def test_remote_security_helpers(self):
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

    @patch("stock_alarm.dashboard_server.recent_equity_trend", return_value=[])
    @patch("stock_alarm.dashboard_server.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.dashboard_server.recent_position_checks", return_value=[])
    @patch("stock_alarm.dashboard_server.recent_price_quality", return_value=[])
    @patch("stock_alarm.dashboard_server.active_strategy_version", return_value={})
    @patch("stock_alarm.dashboard_server.latest_portfolio_risk", return_value={})
    @patch("stock_alarm.dashboard_server.load_sector_mapping", return_value={"086280": "항공화물운송과물류"})
    @patch("stock_alarm.dashboard_server.virtual_trader_state")
    @patch("stock_alarm.dashboard_server.prices", return_value={"086280": 205000})
    def test_trader_payload_adds_sector_to_holdings(self, _prices, state, _sectors, _risk, _strategy, _quality, _checks, _sales, _trend):
        state.return_value = {
            "cash": 500_000, "total_equity": 1_000_000, "holdings_value": 500_000,
            "holdings": [{"ticker": "086280", "valuation": 500_000, "average_price": 200_000, "first_entry_at": "2026-08-01"}],
        }

        payload = trader_payload()

        self.assertEqual("항공화물운송과물류", payload["holdings"][0]["sector"])

    def test_profile_db_path_falls_back_to_aggressive_for_unknown_names(self):
        self.assertEqual(profile_db_path("aggressive"), profile_db_path("not-a-real-profile"))
        self.assertNotEqual(profile_db_path("aggressive"), profile_db_path("neutral"))

    @patch("stock_alarm.dashboard_server.recent_equity_trend", return_value=[])
    @patch("stock_alarm.dashboard_server.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.dashboard_server.recent_position_checks", return_value=[])
    @patch("stock_alarm.dashboard_server.recent_price_quality", return_value=[])
    @patch("stock_alarm.dashboard_server.active_strategy_version", return_value={})
    @patch("stock_alarm.dashboard_server.latest_portfolio_risk", return_value={})
    @patch("stock_alarm.dashboard_server.load_sector_mapping", return_value={})
    @patch("stock_alarm.dashboard_server.virtual_trader_state", return_value={"cash": 0, "holdings": [], "total_equity": 0, "holdings_value": 0})
    @patch("stock_alarm.dashboard_server.prices", return_value={})
    def test_trader_payload_routes_neutral_profile_to_its_own_db(self, prices_mock, state, *_mocks):
        payload = trader_payload("neutral")

        self.assertEqual("neutral", payload["profile"])
        state.assert_called_with({}, path="data/stock_alarm_neutral.db")
        prices_mock.assert_called_with("data/stock_alarm_neutral.db")


class RemoteReadOnlyHandlerTest(unittest.TestCase):
    """The tunnel only ever reaches this handler. It must reject everything
    except a correctly authenticated GET /api/trader -- no dashboard HTML, no
    /remote-setup, no writes, regardless of what Host header the request carries.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RemoteReadOnlyHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        import threading
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def _request(self, path, headers=None, method="GET"):
        request = urllib.request.Request(self.base_url + path, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    @patch.dict("os.environ", {"DASHBOARD_REMOTE_TOKEN": "secret", "DASHBOARD_REMOTE_ORIGIN": "https://leewj1013.github.io"})
    def test_api_trader_rejects_missing_token_even_with_spoofed_host(self):
        status = self._request(
            "/api/trader",
            headers={"Origin": "https://leewj1013.github.io", "Host": "127.0.0.1"},
        )
        self.assertEqual(401, status)

    @patch.dict("os.environ", {"DASHBOARD_REMOTE_TOKEN": "secret", "DASHBOARD_REMOTE_ORIGIN": "https://leewj1013.github.io"})
    @patch("stock_alarm.dashboard_server.trader_payload", return_value={"cash": 0})
    def test_api_trader_accepts_valid_token_and_origin(self, _payload):
        status = self._request(
            "/api/trader",
            headers={"Origin": "https://leewj1013.github.io", "Authorization": "Bearer secret"},
        )
        self.assertEqual(200, status)

    def test_dashboard_and_remote_setup_are_not_served(self):
        self.assertEqual(404, self._request("/"))
        self.assertEqual(404, self._request("/remote-setup"))

    def test_writes_are_rejected(self):
        self.assertEqual(403, self._request("/api/trader/buy", method="POST"))


if __name__ == "__main__":
    unittest.main()
