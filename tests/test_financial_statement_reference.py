import json
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.financial_statement_reference import (
    _account_values,
    _growth_pct,
    _ratio_pct,
    fetch_account_summary,
    financial_ratios,
)


ACCOUNT_ROWS = [
    {"account_nm": "매출액", "fs_div": "CFS", "thstrm_amount": "1,000,000", "frmtrm_amount": "800,000"},
    {"account_nm": "매출액", "fs_div": "OFS", "thstrm_amount": "900,000", "frmtrm_amount": "700,000"},
    {"account_nm": "영업이익", "fs_div": "CFS", "thstrm_amount": "100,000", "frmtrm_amount": "50,000"},
    {"account_nm": "당기순이익", "fs_div": "CFS", "thstrm_amount": "80,000", "frmtrm_amount": "40,000"},
    {"account_nm": "자산총계", "fs_div": "CFS", "thstrm_amount": "5,000,000", "frmtrm_amount": "4,000,000"},
    {"account_nm": "부채총계", "fs_div": "CFS", "thstrm_amount": "2,000,000", "frmtrm_amount": "1,800,000"},
    {"account_nm": "자본총계", "fs_div": "CFS", "thstrm_amount": "3,000,000", "frmtrm_amount": "2,200,000"},
]


class FinancialStatementReferenceTest(unittest.TestCase):
    def test_account_values_prefers_consolidated_statement(self):
        current, prior = _account_values(ACCOUNT_ROWS, "매출액")
        self.assertEqual((1_000_000.0, 800_000.0), (current, prior))

    def test_account_values_missing_account_returns_none(self):
        self.assertEqual((None, None), _account_values(ACCOUNT_ROWS, "미분류계정"))

    def test_growth_and_ratio_helpers_handle_missing_or_zero_inputs(self):
        self.assertIsNone(_growth_pct(100, None))
        self.assertIsNone(_growth_pct(100, 0))
        self.assertEqual(25.0, _growth_pct(125, 100))
        self.assertIsNone(_ratio_pct(None, 100))
        self.assertIsNone(_ratio_pct(100, 0))
        self.assertEqual(50.0, _ratio_pct(50, 100))

    @patch.dict("os.environ", {"DART_API_KEY": "key"})
    @patch("stock_alarm.financial_statement_reference.corp_code_by_stock", return_value="00126380")
    @patch("stock_alarm.financial_statement_reference.urllib.request.urlopen")
    def test_fetch_account_summary_parses_growth_inputs(self, urlopen, _corp_code):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({"list": ACCOUNT_ROWS}).encode("utf-8")
        summary = fetch_account_summary("005930", year=2025)
        self.assertEqual(1_000_000.0, summary["revenue"])
        self.assertEqual(800_000.0, summary["revenue_prev"])
        self.assertEqual(100_000.0, summary["operating_income"])
        self.assertEqual(3_000_000.0, summary["equity"])

    def test_fetch_account_summary_without_api_key_returns_empty(self):
        with patch.dict("os.environ", {"DART_API_KEY": ""}):
            self.assertEqual({}, fetch_account_summary("005930"))

    @patch.dict("os.environ", {"DART_FINANCIALS_LOOKUP": "0"})
    def test_financial_ratios_disabled_by_default(self):
        self.assertEqual({}, financial_ratios("005930"))

    @patch.dict("os.environ", {"DART_FINANCIALS_LOOKUP": "1"})
    @patch("stock_alarm.financial_statement_reference.fetch_account_summary", return_value={
        "revenue": 1_000_000.0, "revenue_prev": 800_000.0,
        "operating_income": 100_000.0, "operating_income_prev": 50_000.0,
        "net_income": 80_000.0, "equity": 3_000_000.0, "liabilities": 2_000_000.0,
    })
    def test_financial_ratios_computes_and_caches(self, fetch):
        with tempfile.TemporaryDirectory() as directory:
            ratios = financial_ratios("005930", cache_dir=directory)
            self.assertAlmostEqual(2.6667, ratios["roe_pct"], places=3)
            self.assertAlmostEqual(66.6667, ratios["debt_ratio_pct"], places=3)
            self.assertAlmostEqual(10.0, ratios["operating_margin_pct"], places=3)
            self.assertAlmostEqual(25.0, ratios["revenue_growth_pct"], places=3)
            fetch.assert_called_once()
            # Second call within the TTL window should hit the cache, not refetch.
            financial_ratios("005930", cache_dir=directory)
            fetch.assert_called_once()

    @patch.dict("os.environ", {"DART_FINANCIALS_LOOKUP": "1"})
    @patch("stock_alarm.financial_statement_reference.fetch_account_summary", return_value={})
    def test_financial_ratios_empty_summary_returns_empty_dict(self, _fetch):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual({}, financial_ratios("005930", cache_dir=directory))


if __name__ == "__main__":
    unittest.main()
