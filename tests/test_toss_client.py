import json
import io
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from stock_alarm.toss_client import TossClient, all_warnings_for, blocking_warnings_for, candles_to_naver_rows, latest_close_for, reset_shared_client, validated_base_url


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TossClientTest(unittest.TestCase):
    def tearDown(self):
        reset_shared_client()

    @patch("stock_alarm.toss_client._urlopen")
    def test_token_uses_form_credentials_and_is_cached(self, urlopen):
        urlopen.return_value = FakeResponse({"access_token": "token", "expires_in": 86400})
        client = TossClient("client-id", "client-secret")
        self.assertEqual("token", client.access_token())
        self.assertEqual("token", client.access_token())
        self.assertEqual(1, urlopen.call_count)
        request = urlopen.call_args.args[0]
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(["client_credentials"], form["grant_type"])
        self.assertEqual(["client-id"], form["client_id"])
        self.assertEqual(["client-secret"], form["client_secret"])

    @patch("stock_alarm.toss_client._urlopen")
    def test_connection_check_is_read_only_and_redacts_account_details(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": [{"symbol": "005930", "lastPrice": "70000", "currency": "KRW"}]}),
            FakeResponse({"result": [{"accountSeq": 123456, "accountType": "BROKERAGE"}]}),
        ]
        result = TossClient("client-id", "client-secret").connection_check()
        self.assertTrue(result["authenticated"])
        self.assertEqual(1, result["account_count"])
        self.assertFalse(result["trading_enabled"])
        self.assertNotIn("accountSeq", json.dumps(result))
        urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertFalse(any("/orders" in url for url in urls))

    @patch("stock_alarm.toss_client._urlopen")
    def test_holdings_sends_the_account_header_and_returns_the_overview(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"items": [{"symbol": "005930", "quantity": "10"}]}}),
        ]
        result = TossClient("client-id", "client-secret").holdings(123456)
        self.assertEqual([{"symbol": "005930", "quantity": "10"}], result["items"])
        request = urlopen.call_args.args[0]
        self.assertEqual("123456", request.get_header("X-tossinvest-account"))
        self.assertIn("/api/v1/holdings", request.full_url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_buying_power_sends_currency_and_account_header(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"currency": "KRW", "cashBuyingPower": "5000000"}}),
        ]
        result = TossClient("client-id", "client-secret").buying_power(123456)
        self.assertEqual("5000000", result["cashBuyingPower"])
        request = urlopen.call_args.args[0]
        self.assertEqual("123456", request.get_header("X-tossinvest-account"))
        self.assertIn("currency=KRW", request.full_url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_sellable_quantity_sends_symbol_and_account_header(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"symbol": "005930", "sellableQuantity": "10"}}),
        ]
        result = TossClient("client-id", "client-secret").sellable_quantity(123456, "005930")
        self.assertEqual("10", result["sellableQuantity"])
        request = urlopen.call_args.args[0]
        self.assertEqual("123456", request.get_header("X-tossinvest-account"))
        self.assertIn("symbol=005930", request.full_url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_commissions_returns_a_list(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": [{"market": "KR", "rate": "0.015"}]}),
        ]
        result = TossClient("client-id", "client-secret").commissions(123456)
        self.assertEqual([{"market": "KR", "rate": "0.015"}], result)

    @patch("stock_alarm.toss_client._urlopen")
    def test_order_history_requires_status_and_sends_account_header(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"orders": [], "nextCursor": None}}),
        ]
        result = TossClient("client-id", "client-secret").order_history(123456, "OPEN", symbol="005930")
        self.assertEqual([], result["orders"])
        request = urlopen.call_args.args[0]
        self.assertEqual("123456", request.get_header("X-tossinvest-account"))
        self.assertIn("status=OPEN", request.full_url)
        self.assertIn("symbol=005930", request.full_url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_order_detail_sends_account_header(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"orderId": "abc", "status": "FILLED"}}),
        ]
        result = TossClient("client-id", "client-secret").order_detail(123456, "abc")
        self.assertEqual("FILLED", result["status"])
        request = urlopen.call_args.args[0]
        self.assertEqual("123456", request.get_header("X-tossinvest-account"))
        self.assertIn("/api/v1/orders/abc", request.full_url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_stock_warnings_needs_no_account_header(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": [{"warningType": "LIQUIDATION_TRADING", "startDate": "2026-03-26"}]}),
        ]
        result = TossClient("client-id", "client-secret").stock_warnings("033340")
        self.assertEqual(["LIQUIDATION_TRADING"], [row["warningType"] for row in result])
        request = urlopen.call_args.args[0]
        self.assertIsNone(request.get_header("X-tossinvest-account"))
        self.assertIn("/api/v1/stocks/033340/warnings", request.full_url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_candles_defaults_to_daily_adjusted(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"candles": [{"closePrice": "70000"}]}}),
        ]
        result = TossClient("client-id", "client-secret").candles("005930")
        self.assertEqual([{"closePrice": "70000"}], result["candles"])
        url = urlopen.call_args.args[0].full_url
        self.assertIn("interval=1d", url)
        self.assertIn("adjusted=true", url)

    @patch("stock_alarm.toss_client._urlopen")
    def test_blocking_warnings_excludes_common_short_lived_flags(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": [{"warningType": "VI_STATIC"}, {"warningType": "INVESTMENT_RISK"}]}),
        ]
        result = TossClient("client-id", "client-secret").blocking_warnings("033340")
        self.assertEqual({"INVESTMENT_RISK"}, result)

    def test_candles_to_naver_rows_converts_and_sorts_oldest_first(self):
        candles = [
            {"timestamp": "2026-09-11T09:00:00+09:00", "openPrice": "70000", "highPrice": "71000", "lowPrice": "69500", "closePrice": "70500", "volume": "1000000"},
            {"timestamp": "2026-09-10T09:00:00+09:00", "openPrice": "69000", "highPrice": "70000", "lowPrice": "68500", "closePrice": "69800", "volume": "900000"},
        ]
        rows = candles_to_naver_rows(candles)
        self.assertEqual(["20260910", "20260911"], [row[0] for row in rows])
        self.assertEqual([69000, 70000, 68500, 69800, 900000], rows[0][1:])

    def test_candles_to_naver_rows_skips_unparseable_entries(self):
        self.assertEqual([], candles_to_naver_rows([{"timestamp": "2026-09-11T09:00:00+09:00"}]))

    def test_blocking_warnings_for_tolerates_missing_credentials(self):
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "", "TOSS_CLIENT_SECRET": ""}, clear=False):
            self.assertEqual(set(), blocking_warnings_for("033340"))

    @patch("stock_alarm.toss_client._urlopen")
    def test_all_warnings_for_includes_non_blocking_flags(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": [{"warningType": "OVERHEATED"}]}),
        ]
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "id", "TOSS_CLIENT_SECRET": "secret"}):
            self.assertEqual({"OVERHEATED"}, all_warnings_for("033340"))

    def test_all_warnings_for_tolerates_missing_credentials(self):
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "", "TOSS_CLIENT_SECRET": ""}, clear=False):
            self.assertEqual(set(), all_warnings_for("033340"))

    @patch("stock_alarm.toss_client._urlopen")
    def test_latest_close_for_reads_the_most_recent_candle(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"candles": [{"closePrice": "70500"}]}}),
        ]
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "id", "TOSS_CLIENT_SECRET": "secret"}):
            self.assertEqual(70500, latest_close_for("033340"))

    def test_latest_close_for_tolerates_missing_credentials(self):
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "", "TOSS_CLIENT_SECRET": ""}, clear=False):
            self.assertIsNone(latest_close_for("033340"))

    @patch("stock_alarm.toss_client._urlopen")
    def test_convenience_helpers_share_one_cached_access_token(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"candles": [{"closePrice": "70500"}]}}),
            FakeResponse({"result": []}),
        ]
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "id", "TOSS_CLIENT_SECRET": "secret"}):
            self.assertEqual(70500, latest_close_for("005930"))
            self.assertEqual(set(), blocking_warnings_for("005930"))
        self.assertEqual(3, urlopen.call_count)

    @patch("stock_alarm.toss_client.time.sleep")
    @patch("stock_alarm.toss_client._urlopen")
    def test_rate_limit_response_retries_with_retry_after(self, urlopen, sleep):
        rate_limit = urllib.error.HTTPError(
            "https://example.test", 429, "Too Many Requests", {"Retry-After": "0.5"}, io.BytesIO(b"{}"),
        )
        urlopen.side_effect = [rate_limit, FakeResponse({"result": []})]
        client = TossClient("client-id", "client-secret")
        client._access_token = "token"
        client._expires_at = __import__("time").time() + 3600

        self.assertEqual([], client.prices(["005930"]))
        sleep.assert_called_once_with(0.5)

    @patch("stock_alarm.toss_client._urlopen")
    def test_market_calendar_kr(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({"result": {"today": {"date": "2026-09-12", "integrated": None}}}),
        ]
        result = TossClient("client-id", "client-secret").market_calendar_kr()
        self.assertIsNone(result["today"]["integrated"])

    def test_base_url_is_limited_to_the_exact_toss_origin(self):
        self.assertEqual("https://openapi.tossinvest.com", validated_base_url("https://openapi.tossinvest.com/"))
        for unsafe in (
            "http://openapi.tossinvest.com",
            "https://openapi.tossinvest.com.evil.example",
            "https://openapi.tossinvest.com@evil.example",
            "https://openapi.tossinvest.com:444",
            "https://openapi.tossinvest.com/path",
            "https://openapi.tossinvest.com?next=evil",
            "https://openapi.tossinvest.com./",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                validated_base_url(unsafe)


if __name__ == "__main__":
    unittest.main()
