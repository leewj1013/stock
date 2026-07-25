import os
import unittest
from unittest.mock import patch

from stock_alarm.health import enabled, lines, yes


class HealthTest(unittest.TestCase):
    def test_yes(self):
        self.assertEqual("ok", yes(True))
        self.assertEqual("missing", yes(False))

    def test_enabled(self):
        old = os.environ.get("X_ENABLED")
        try:
            os.environ["X_ENABLED"] = "1"
            self.assertEqual("on", enabled("X_ENABLED"))
            os.environ["X_ENABLED"] = "0"
            self.assertEqual("off", enabled("X_ENABLED"))
        finally:
            if old is None:
                os.environ.pop("X_ENABLED", None)
            else:
                os.environ["X_ENABLED"] = old

    @patch("stock_alarm.health.latest_naver_trading_day")
    @patch("stock_alarm.health.task_error_status", return_value="task_error=old")
    @patch("stock_alarm.health.configured_stocks", return_value={"005930": "Samsung"})
    @patch.dict(os.environ, {"DART_LOOKUP": "1", "DART_API_KEY": "x", "DART_SCORE_WEIGHT": "3", "NEWS_SCORE_WEIGHT": "2"}, clear=True)
    def test_lines_includes_external_signal_settings(self, _stocks, _task_error, _day):
        text = "\n".join(lines())

        self.assertIn("DART_LOOKUP=on", text)
        self.assertIn("DART_API_KEY=ok", text)
        self.assertIn("DART_SCORE_WEIGHT=3", text)
        self.assertIn("NEWS_SCORE_WEIGHT=2", text)
        self.assertIn("task_error=old", text)


if __name__ == "__main__":
    unittest.main()
