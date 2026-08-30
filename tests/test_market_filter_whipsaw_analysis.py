import unittest

from stock_alarm.market_filter_whipsaw_analysis import fixed_window_stats, ma20_recovery


class FakeRecoveryEngine:
    def __init__(self):
        self.by_date = {"A": {}}
        for index in range(1, 24):
            day = f"2022-01-{index:02d}"
            close = 9.0 if index <= 21 else 11.0
            row = [day.replace("-", ""), close, close, close, close, 100]
            self.by_date["A"][day] = (index - 1, row)

    def _history(self, ticker, day, length=20):
        index = self.by_date[ticker][day][0]
        ordered = [item[1] for item in self.by_date[ticker].values()]
        return ordered[max(0, index - length + 1):index + 1]


class MarketWhipsawAnalysisTest(unittest.TestCase):
    def test_fixed_window_mdd_and_return(self):
        rows = [{"date": "2022-01-01", "equity": 100}, {"date": "2022-01-02", "equity": 90},
                {"date": "2022-01-03", "equity": 99}]
        result = fixed_window_stats(rows, "2022-01-01", "2022-01-03")
        self.assertAlmostEqual(-1.0, result["return_pct"])
        self.assertAlmostEqual(-10.0, result["mdd_pct"])

    def test_ma20_recovery_uses_only_post_sale_days(self):
        result = ma20_recovery(FakeRecoveryEngine(), {"ticker": "A", "date": "2022-01-20"}, horizons=(1, 3))
        self.assertFalse(result["recovered_1d"])
        self.assertTrue(result["recovered_3d"])


if __name__ == "__main__":
    unittest.main()
