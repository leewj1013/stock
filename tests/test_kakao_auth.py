import unittest
from urllib.parse import parse_qs, urlparse

from stock_alarm.kakao_auth import auth_url


class KakaoAuthTest(unittest.TestCase):
    def test_auth_url_includes_talk_message_scope(self):
        url = auth_url("key", "https://example.com/oauth")
        query = parse_qs(urlparse(url).query)

        self.assertEqual(["key"], query["client_id"])
        self.assertEqual(["https://example.com/oauth"], query["redirect_uri"])
        self.assertEqual(["talk_message"], query["scope"])


if __name__ == "__main__":
    unittest.main()
