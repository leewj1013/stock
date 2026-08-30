import unittest
import csv
import tempfile
from pathlib import Path

from stock_alarm.expanded_factor_analysis import (
    estimated_power,
    parse_market_cap_page,
    required_periods_for_power,
    summarize_period_quality,
)


class ExpandedFactorAnalysisTest(unittest.TestCase):
    def test_market_cap_parser_keeps_equity_and_excludes_zero_face_value_fund(self):
        content = """
        <table>
          <tr><td>1</td><td><a class="tltle" href="/item/main.naver?code=005930">삼성전자</a></td>
              <td>100</td><td>0</td><td>0%</td><td>100</td><td>1,000,000</td></tr>
          <tr><td>2</td><td><a class="tltle" href="/item/main.naver?code=069500">KODEX 200</a></td>
              <td>100</td><td>0</td><td>0%</td><td>0</td><td>500,000</td></tr>
        </table>
        """
        rows = parse_market_cap_page(content, "KOSPI")
        self.assertEqual(1, len(rows))
        self.assertEqual("005930", rows[0]["ticker"])
        self.assertEqual(1_000_000, rows[0]["market_cap_100m_krw"])

    def test_power_analysis_known_normal_approximation(self):
        # Two-sided alpha=.05, power=.80, d=.20 -> ceil(196.22...) = 197.
        self.assertEqual(197, required_periods_for_power(0.2, 0.05, 0.8, 1))
        self.assertGreaterEqual(estimated_power(0.2, 197, 0.05, 1), 0.8)

    def test_multiple_test_power_requires_more_periods(self):
        single = required_periods_for_power(0.2, 0.05, 0.8, 1)
        corrected = required_periods_for_power(0.2, 0.05, 0.8, 80)
        self.assertGreater(corrected, single)

    def test_period_quality_counts_only_rows_before_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "000001.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["date", "close"])
                writer.writeheader()
                writer.writerows([
                    {"date": "20211230", "close": "100"},
                    {"date": "20220103", "close": "101"},
                ])
            result = summarize_period_quality(
                Path(temporary_directory),
                [{"date": "20211231", "reason": "invalid_ohlc"}, {"date": "20220104", "reason": "invalid_ohlc"}],
                "20220101",
            )
        self.assertEqual({"accepted": 1, "rejected": 1, "rejection_rate_pct": 50.0}, result)


if __name__ == "__main__":
    unittest.main()
