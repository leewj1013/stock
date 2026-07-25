import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.open_check import lines, main


class OpenCheckTest(unittest.TestCase):
    @patch("stock_alarm.open_check.run_log_statuses", return_value=["recommendations=ok"])
    @patch("stock_alarm.open_check.status", return_value="daily ok: telegram at now")
    def test_lines_checks_recommendations_only(self, _status, _logs):
        self.assertEqual(["daily ok: telegram at now", "recommendations=ok"], lines(date(2026, 7, 25)))

    @patch("stock_alarm.open_check.lines", return_value=["daily ok: telegram at now", "recommendations=ok"])
    def test_main_ok(self, _lines):
        self.assertEqual(0, main())

    @patch("stock_alarm.open_check.lines", return_value=["daily ok: telegram at now", "recommendations=missing"])
    def test_main_fails_when_recommendations_missing(self, _lines):
        self.assertEqual(1, main())


if __name__ == "__main__":
    unittest.main()
