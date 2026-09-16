import json
import os
import unittest
from unittest.mock import patch

from stock_alarm.fundamental_reference import _naver_number, naver_snapshot, snapshot

PAYLOAD = {
    "stockName": "삼성전자",
    "totalInfos": [
        {"code": "marketValue", "key": "시총", "value": "1,482조 316억"},
        {"code": "per", "key": "PER", "value": "11.37배", "valueDesc": "2026.06."},
        {"code": "eps", "key": "EPS", "value": "22,292원"},
        {"code": "pbr", "key": "PBR", "value": "2.95배"},
        {"code": "bps", "key": "BPS", "value": "86,052원"},
        {"code": "dividendYieldRatio", "key": "배당수익률", "value": "0.66%"},
    ],
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FundamentalReferenceTest(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_disabled_by_default(self):
        self.assertEqual("disabled", snapshot("005930")["financial_notes"])

    def test_naver_number_strips_korean_units(self):
        self.assertEqual(11.37, _naver_number("11.37배"))
        self.assertEqual(22292.0, _naver_number("22,292원"))
        self.assertEqual(0.66, _naver_number("0.66%"))
        self.assertEqual(0.0, _naver_number("N/A"))
        self.assertEqual(0.0, _naver_number(None))

    def test_snapshot_reads_the_mobile_api_fields(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(PAYLOAD)):
            result = naver_snapshot("005930")

        self.assertEqual(11.37, result["per"])
        self.assertEqual(2.95, result["pbr"])
        self.assertEqual(0.66, result["dividend_yield"])
        self.assertEqual(22292.0, result["eps"])
        self.assertEqual("삼성전자", result["name"])
        self.assertEqual("naver mobile stock api", result["financial_notes"])

    def test_missing_values_report_unavailable_instead_of_zero_ratios(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse({"stockName": "X", "totalInfos": []})):
            result = naver_snapshot("000000")

        self.assertEqual(0.0, result["financial_score"])
        self.assertEqual("naver fundamentals unavailable", result["financial_notes"])
        self.assertNotIn("per", result)


if __name__ == "__main__":
    unittest.main()
