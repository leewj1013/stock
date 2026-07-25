import unittest
from unittest.mock import patch

from stock_alarm.app import Pick, format_message


class FormatMessageTest(unittest.TestCase):
    def test_includes_ticker_score_and_disclaimer(self):
        message = format_message(
            [
                Pick(
                    ticker="005930",
                    name="Samsung",
                    close=80000,
                    volume_ratio=2.3,
                    trading_value=123_000_000_000,
                    score=76.5,
                )
            ]
        )

        self.assertIn("Samsung(005930)", message)
        self.assertIn("005930", message)
        self.assertIn("76.5", message)
        self.assertIn("2.3", message)

    @patch("stock_alarm.app.performance_penalty", return_value=4)
    def test_includes_performance_penalty_when_present(self, _penalty):
        message = format_message(
            [
                Pick(
                    ticker="005930",
                    name="Samsung",
                    close=80000,
                    volume_ratio=2.3,
                    trading_value=123_000_000_000,
                    score=76.5,
                )
            ]
        )

        self.assertIn("성과 감점: -4.0점", message)


if __name__ == "__main__":
    unittest.main()
