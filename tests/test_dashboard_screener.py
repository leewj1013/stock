import unittest
from unittest.mock import patch

from stock_alarm.dashboard import screener_caption, screener_rows

RESULT = {
    "generated_at": "2026-09-17T09:30:00",
    "as_of": "20260916",
    "conditions": "PER 15배 미만 · 매출성장률 5% 초과 · 잉여현금흐름 양수",
    "evaluated": 400, "total": 442, "incomplete": 42,
    "matches": [
        {"ticker": "000270", "name": "기아", "market": "KOSPI", "per": 6.23, "pbr": 0.77,
         "free_cash_flow": 5_666_400_000_000.0, "revenue_growth_pct": 12.55,
         "operating_income_growth_pct": -4.9, "period": "2026Q2"},
        {"ticker": "000001", "name": "자료일부없음", "market": "KOSDAQ", "per": 9.0, "pbr": None,
         "free_cash_flow": None, "revenue_growth_pct": 7.0,
         "operating_income_growth_pct": None, "period": "2026Q2"},
    ],
}


class DashboardScreenerTest(unittest.TestCase):
    @patch("stock_alarm.dashboard.screener_result", return_value=RESULT)
    def test_rows_are_formatted_for_display(self, _result):
        rows = screener_rows()
        self.assertEqual("기아", rows[0]["name"])
        self.assertEqual("6.23", rows[0]["per"])
        self.assertEqual("56,664억", rows[0]["free_cash_flow"])
        self.assertEqual("12.6", rows[0]["revenue_growth_pct"])
        self.assertEqual("-4.9", rows[0]["operating_income_growth_pct"])

    @patch("stock_alarm.dashboard.screener_result", return_value=RESULT)
    def test_missing_values_render_blank_not_zero(self, _result):
        row = screener_rows()[1]
        self.assertEqual("", row["pbr"])
        self.assertEqual("", row["free_cash_flow"])
        self.assertEqual("", row["operating_income_growth_pct"])

    @patch("stock_alarm.dashboard.screener_result", return_value=RESULT)
    def test_caption_states_conditions_and_coverage(self, _result):
        caption = screener_caption()
        self.assertIn("PER 15배 미만", caption)
        self.assertIn("평가 400/442종목", caption)
        self.assertIn("자료부족 42", caption)

    @patch("stock_alarm.dashboard.screener_result", return_value={})
    def test_never_run_says_so_instead_of_pretending_zero_matches(self, _result):
        self.assertEqual([], screener_rows())
        self.assertEqual("아직 실행하지 않았습니다.", screener_caption())


if __name__ == "__main__":
    unittest.main()
