import unittest

from stock_alarm.app import Pick, format_message, reason_summary


class FormatMessageTest(unittest.TestCase):
    def pick(self, news=0, disclosure=0, penalty=0):
        return Pick("005930", "Samsung", 80000, 2.3, 123_000_000_000, 76.5, news_score=news, disclosure_score=disclosure, performance_penalty=penalty)

    def test_includes_ticker_score_and_disclaimer(self):
        message = format_message([self.pick()])
        self.assertIn("Samsung(005930)", message)
        self.assertIn("거래량 2.3배", message)
        self.assertIn("사유 요약: 거래량 급증", message)
        self.assertIn("투자 자문이 아닙니다", message)

    def test_reason_summary(self):
        self.assertEqual("기본 조건 충족", reason_summary(1.5, 0, 0, 0))
        self.assertEqual("거래량 급증 + 뉴스 보너스 + 공시 보너스 + 성과 감점", reason_summary(2.1, 1, 1, 1))

    def test_includes_captured_external_scores(self):
        message = format_message([self.pick(news=2, disclosure=3, penalty=4)])
        self.assertIn("뉴스 보너스: +2.0점", message)
        self.assertIn("공시 보너스: +3.0점", message)
        self.assertIn("성과 감점: -4.0점", message)


if __name__ == "__main__":
    unittest.main()
