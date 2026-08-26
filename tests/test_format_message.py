import unittest
from unittest.mock import patch

from stock_alarm.app import Pick, allocation_percentages, format_message, reason_summary


class FormatMessageTest(unittest.TestCase):
    def test_allocation_percentages_are_position_targets_and_penalize_volatility(self):
        low_vol = self.pick()
        high_vol = Pick(**{**low_vol.__dict__, "ticker": "B", "atr20_pct": 4})
        allocations = allocation_percentages([low_vol, high_vol], performance_path="missing.csv")

        self.assertLessEqual(sum(allocations), 60)
        self.assertTrue(all(10 <= value <= 30 for value in allocations))
        self.assertGreater(allocations[0], allocations[1])

    @patch("stock_alarm.app.historical_allocation_factors", return_value={"B": 1.5})
    def test_allocation_learns_from_historical_performance(self, _factors):
        first = self.pick()
        second = Pick(**{**first.__dict__, "ticker": "B"})
        allocations = allocation_percentages([first, second])

        self.assertGreater(allocations[1], allocations[0])

    def pick(self, news=0, disclosure=0, penalty=0):
        return Pick("005930", "Samsung", 80000, 2.3, 123_000_000_000, 76.5, news_score=news, disclosure_score=disclosure, performance_penalty=penalty)

    def test_includes_ticker_score_and_disclaimer(self):
        message = format_message([self.pick()])
        self.assertIn("가상투자 비중: 전체 가상계좌 자산 기준", message)
        self.assertIn("가상투자 예정 22.95%", message)
        self.assertIn("Samsung(005930)", message)
        self.assertIn("거래량 2.3배", message)
        self.assertIn("신호: 거래량 급증", message)
        self.assertIn("투자 자문이 아닙니다", message)

    def test_reason_summary(self):
        self.assertEqual("기본 조건 충족", reason_summary(1.5, 0, 0, 0))
        self.assertEqual("거래량 급증 + 뉴스 보너스 + 공시 보너스 + 성과 감점", reason_summary(2.1, 1, 1, 1))

    def test_includes_captured_external_scores(self):
        message = format_message([self.pick(news=2, disclosure=3, penalty=4)])
        self.assertIn("뉴스 보너스", message)
        self.assertIn("공시 보너스", message)
        self.assertIn("성과 감점", message)

    def test_includes_virtual_execution_summary(self):
        result = {"spent": 80000, "cash": 20000, "executions": [{"ticker": "005930", "quantity": 1, "cost": 80000}]}
        message = format_message([self.pick()], result)

        self.assertIn("가상 자동매수", message)
        self.assertIn("1주 · 80,000원", message)
        self.assertIn("잔여 현금 20,000원", message)


if __name__ == "__main__":
    unittest.main()
