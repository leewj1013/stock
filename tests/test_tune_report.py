import unittest

from stock_alarm.tune_report import best_row, confidence


class TuneReportTest(unittest.TestCase):
    def test_best_row_uses_return_then_win_rate(self):
        rows = [
            {"volume_multiplier": "1.5", "hold_days": "1", "picks": "10", "avg_return_pct": "1.00", "win_rate_pct": "40.0"},
            {"volume_multiplier": "2.0", "hold_days": "3", "picks": "8", "avg_return_pct": "1.00", "win_rate_pct": "50.0"},
        ]

        self.assertEqual("2.0", best_row(rows)["volume_multiplier"])

    def test_confidence_weak_when_few_picks(self):
        self.assertEqual("weak", confidence({"picks": "1"}))


if __name__ == "__main__":
    unittest.main()
