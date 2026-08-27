import os
import tempfile
import unittest
from datetime import date, datetime

from stock_alarm.data_quality import checked_prices, validate_price_rows


class DataQualityTest(unittest.TestCase):
    def test_rejects_stale_and_invalid_ohlc(self):
        stale = validate_price_rows("A", [["20260826", 100, 110, 90, 105, 10]], date(2026, 8, 27))
        invalid = validate_price_rows("A", [["20260827", 100, 90, 110, 105, 10]], date(2026, 8, 27))
        self.assertEqual("stale", stale["status"])
        self.assertEqual("invalid", invalid["status"])

    def test_checked_prices_returns_only_valid_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            rows = {"A": [["20260827", 100, 110, 90, 105, 10]], "B": []}
            prices, checks = checked_prices(["A", "B"], lambda ticker: rows[ticker], date(2026, 8, 27), path)
            self.assertEqual({"A": 105}, prices)
            self.assertEqual(["valid", "invalid"], [row["status"] for row in checks])

    def test_provider_failure_is_recorded_instead_of_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            prices, checks = checked_prices(["A"], lambda _ticker: (_ for _ in ()).throw(OSError("offline")), date(2026, 8, 27), path)
            self.assertEqual({}, prices)
            self.assertEqual("invalid", checks[0]["status"])
            self.assertIn("provider_error", checks[0]["reason"])


if __name__ == "__main__":
    unittest.main()
