import json
import unittest
import urllib.parse
from unittest.mock import patch

from stock_alarm.toss_client import TossClient


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
    @patch("stock_alarm.toss_client.urllib.request.urlopen")
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

    @patch("stock_alarm.toss_client.urllib.request.urlopen")
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


if __name__ == "__main__":
    unittest.main()

