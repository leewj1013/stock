import unittest
from unittest.mock import patch

from stock_alarm.app import Pick, format_message, reason_summary


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
        self.assertIn("사유 요약: 거래량 급증", message)

    def test_reason_summary(self):
        self.assertEqual("기본 조건 충족", reason_summary(1.5, 0, 0, 0))
        self.assertEqual("거래량 급증 + 뉴스 보너스 + 공시 보너스 + 성과 감점", reason_summary(2.1, 1, 1, 1))

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

    @patch("stock_alarm.app.dart_bonus", return_value=3)
    def test_includes_dart_bonus_when_present(self, _bonus):
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

        self.assertIn("공시 보너스: +3.0점", message)

    @patch("stock_alarm.app.news_bonus", return_value=2)
    def test_includes_news_bonus_when_present(self, _bonus):
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

        self.assertIn("뉴스 보너스: +2.0점", message)


if __name__ == "__main__":
    unittest.main()
