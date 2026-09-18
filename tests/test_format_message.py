import unittest
from unittest.mock import patch

from stock_alarm.app import (
    Pick,
    allocation_percentages,
    correlation_limited_allocations,
    format_message,
    reason_summary,
)


class FormatMessageTest(unittest.TestCase):
    def test_connected_high_correlation_group_is_capped_at_40_percent(self):
        picks = [Pick(**{**self.pick().__dict__, "ticker": ticker}) for ticker in ("A", "B", "C")]
        closes = [100.0]
        for index in range(1, 62):
            closes.append(closes[-1] * (1.01 if index % 3 else 0.995))
        rows = [[f"202201{index:02d}", close, close, close, close, 100] for index, close in enumerate(closes)]
        with patch.dict("os.environ", {
            "CORRELATION_LIMIT": "0.8", "CORRELATED_GROUP_MAX_PCT": "40",
            "VIRTUAL_TRADER_MIN_POSITION_PCT": "10",
        }):
            limited = correlation_limited_allocations(
                picks, [30.0, 30.0, 30.0], price_rows_by_ticker={pick.ticker: rows for pick in picks},
            )
        self.assertLessEqual(sum(limited), 40.0)
        self.assertEqual([0.0, 10.0, 30.0], sorted(limited))

    def test_allocation_percentages_are_position_targets_and_penalize_volatility(self):
        low_vol = self.pick()
        high_vol = Pick(**{**low_vol.__dict__, "ticker": "B", "atr20_pct": 4})
        with patch.dict("os.environ", {"VIRTUAL_TRADER_POSITION_SIZING_MODE": "dynamic"}):
            allocations = allocation_percentages([low_vol, high_vol], performance_path="missing.csv")

        self.assertLessEqual(sum(allocations), 60)
        self.assertTrue(all(10 <= value <= 30 for value in allocations))
        self.assertGreater(allocations[0], allocations[1])

    @patch("stock_alarm.app.historical_allocation_factors", return_value={"B": 1.5})
    def test_allocation_learns_from_historical_performance(self, _factors):
        # atr20_pct=6 keeps the volatility-only baseline below the ceiling so
        # the learned-performance multiplier has visible room to differentiate.
        first = Pick(**{**self.pick().__dict__, "atr20_pct": 6})
        second = Pick(**{**first.__dict__, "ticker": "B"})
        with patch.dict("os.environ", {"VIRTUAL_TRADER_POSITION_SIZING_MODE": "dynamic"}):
            allocations = allocation_percentages([first, second])

        self.assertGreater(allocations[1], allocations[0])

    def pick(self, news=0, disclosure=0, penalty=0):
        return Pick("005930", "Samsung", 80000, 2.3, 123_000_000_000, 76.5, news_score=news, disclosure_score=disclosure, performance_penalty=penalty)

    def test_unbought_recommendation_shows_exit_levels_not_internal_score(self):
        message = format_message([self.pick()])
        self.assertIn("[매수 추천", message)
        self.assertIn("Samsung(005930)", message)
        self.assertIn("현재가 80,000원 · 목표 비중 10%", message)
        # stop is the wider of SELL_LOSS_PCT (5%) and ATR x2; target is TAKE_PROFIT_1_PCT (10%)
        self.assertIn("손절 76,000원(-5.0%) · 1차 익절 88,000원(+10%)", message)
        self.assertIn("신호: 거래량 급증 · 거래량 2.3배", message)
        self.assertIn("투자 자문이 아닙니다", message)
        # internal figures a reader cannot act on are gone
        self.assertNotIn("점수", message)
        self.assertNotIn("가상투자 비중", message)
        self.assertNotIn("ATR", message)

    def test_volatile_pick_gets_the_wider_atr_stop_in_plain_words(self):
        message = format_message([Pick(**{**self.pick().__dict__, "atr20_pct": 4.0})])
        self.assertIn("손절 73,600원(-8.0%)", message)
        self.assertIn("하루 변동폭 약 4.0%", message)

    def test_fundamentals_line_and_warnings(self):
        fundamentals = {"005930": {"per": 11.4, "free_cash_flow": -1_667_700_000_000.0, "revenue_growth_pct": 35.5, "market": "KOSPI"}}
        message = format_message([self.pick()], fundamentals=fundamentals)
        self.assertIn("재무: PER 11.4 · 잉여현금흐름 -16,677억 · 매출 +35.5%", message)
        self.assertIn("⚠ 잉여현금흐름 적자", message)

        loss_making = {"005930": {"per": None, "free_cash_flow": 42_000_000_000.0, "market": "KOSDAQ"}}
        self.assertIn("⚠ 적자 기업(PER 없음)", format_message([self.pick()], fundamentals=loss_making))
        self.assertNotIn("재무:", format_message([self.pick()], fundamentals={}))

    def test_reason_summary(self):
        self.assertEqual("기본 조건 충족", reason_summary(1.5, 0, 0, 0))
        self.assertEqual("거래량 급증 + 뉴스 보너스 + 공시 보너스 + 성과 감점", reason_summary(2.1, 1, 1, 1))

    def test_includes_captured_external_scores(self):
        message = format_message([self.pick(news=2, disclosure=3, penalty=4)])
        self.assertIn("뉴스 보너스", message)
        self.assertIn("공시 보너스", message)
        self.assertIn("성과 감점", message)

    def test_bought_picks_lead_and_unbought_ones_collapse_to_a_line(self):
        other = Pick(**{**self.pick().__dict__, "ticker": "000660", "name": "SK hynix"})
        result = {"spent": 80000, "cash": 20000, "executions": [{"ticker": "005930", "price": 80000, "quantity": 1, "cost": 80000}]}
        message = format_message([self.pick(), other], result)

        self.assertIn("[가상매수 체결", message)
        self.assertIn("1종목 · 총 80,000원 · 잔여 현금 20,000원", message)
        self.assertIn("매수 1주 × 80,000원 = 80,000원", message)
        self.assertIn("기타 추천(미매수): SK hynix", message)
        self.assertNotIn("SK hynix(000660)", message)


if __name__ == "__main__":
    unittest.main()
