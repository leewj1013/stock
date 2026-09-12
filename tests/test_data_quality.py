import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

from stock_alarm.data_quality import checked_prices, validate_price_rows


class DataQualityTest(unittest.TestCase):
    def test_rejects_stale_and_invalid_ohlc(self):
        stale = validate_price_rows("A", [["20260826", 100, 110, 90, 105, 10]], date(2026, 8, 27))
        invalid = validate_price_rows("A", [["20260827", 100, 90, 110, 105, 10]], date(2026, 8, 27))
        self.assertEqual("stale", stale["status"])
        self.assertEqual("invalid", invalid["status"])

    @patch("stock_alarm.toss_client.TossClient")
    def test_enriches_the_reason_with_a_confirmed_warning_when_allowed(self, toss_client_cls):
        toss_client_cls.return_value.stock_warnings.return_value = [{"warningType": "LIQUIDATION_TRADING"}]
        result = validate_price_rows("A", [["20260827", 0, 0, 0, 105, 0]], date(2026, 8, 27), allow_external_lookup=True)
        self.assertEqual("invalid_ohlc(경고:LIQUIDATION_TRADING)", result["reason"])

    @patch("stock_alarm.toss_client.TossClient")
    def test_does_not_look_up_warnings_by_default(self, toss_client_cls):
        result = validate_price_rows("A", [["20260827", 0, 0, 0, 105, 0]], date(2026, 8, 27))
        self.assertEqual("invalid_ohlc", result["reason"])
        toss_client_cls.assert_not_called()

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
