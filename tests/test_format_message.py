import unittest

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


if __name__ == "__main__":
    unittest.main()
