import csv
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.sell_performance import outcome_rows


class SellPerformanceTest(unittest.TestCase):
    @patch("stock_alarm.sell_performance.naver_close_after", side_effect=[90, 95, 100, None])
    @patch("stock_alarm.sell_performance.next_execution", return_value=(date(2026, 8, 3), 100))
    def test_tracks_counterfactual_returns_after_sell(self, _execution, _close):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["created_at", "ticker", "name", "entry_price", "close"])
            writer.writerow(["2026-07-31T15:00:00", "005930", "Samsung", "110", "100"])
            path = file.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        rows = outcome_rows(path)
        self.assertAlmostEqual(-10.3, rows[0]["return_1d_pct"])
        self.assertAlmostEqual(-5.3, rows[0]["return_3d_pct"])
        self.assertIsNone(rows[0]["return_10d_pct"])


if __name__ == "__main__":
    unittest.main()
