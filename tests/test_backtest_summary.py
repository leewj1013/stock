import csv
import os
import tempfile
import unittest

from stock_alarm.backtest import write_summary


class BacktestSummaryTest(unittest.TestCase):
    def test_writes_summary_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "summary.csv")
            write_summary(
                [
                    ["2024-01-01", "A", "Alpha", 100, 110, "10.00"],
                    ["2024-01-02", "B", "Beta", 100, 90, "-10.00"],
                ],
                5,
                path,
            )

            with open(path, newline="", encoding="utf-8-sig") as file:
                rows = dict(csv.reader(file))

        self.assertEqual("2", rows["picks"])
        self.assertEqual("0.00", rows["avg_return_pct"])
        self.assertEqual("50.0", rows["win_rate_pct"])


if __name__ == "__main__":
    unittest.main()
