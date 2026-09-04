import unittest

from stock_alarm.app import category_scores, momentum_score


class CategoryScoresTest(unittest.TestCase):
    def test_profitability_and_growth_prefer_real_dart_ratios(self):
        ratios = {"roe_pct": 10.0, "operating_margin_pct": 10.0, "revenue_growth_pct": 10.0, "operating_income_growth_pct": 10.0}
        scores = category_scores(ratios, financial_score=0, disclosure_score_raw=0, dividend_yield=0, atr20_pct=3)
        self.assertEqual(50.0, scores["profitability_score"])
        self.assertEqual(50.0, scores["growth_score"])

    def test_profitability_falls_back_to_financial_score_without_dart_data(self):
        scores = category_scores({}, financial_score=2.5, disclosure_score_raw=0, dividend_yield=0, atr20_pct=3)
        self.assertEqual(50.0, scores["profitability_score"])

    def test_growth_falls_back_to_disclosure_score_without_dart_data(self):
        good_news = category_scores({}, financial_score=0, disclosure_score_raw=3, dividend_yield=0, atr20_pct=3)
        bad_news = category_scores({}, financial_score=0, disclosure_score_raw=-3, dividend_yield=0, atr20_pct=3)
        self.assertGreater(good_news["growth_score"], bad_news["growth_score"])

    def test_stability_penalizes_high_debt_and_high_volatility(self):
        stable = category_scores({"debt_ratio_pct": 20.0}, financial_score=0, disclosure_score_raw=0, dividend_yield=0, atr20_pct=1)
        risky = category_scores({"debt_ratio_pct": 180.0}, financial_score=0, disclosure_score_raw=0, dividend_yield=0, atr20_pct=9)
        self.assertGreater(stable["stability_score"], risky["stability_score"])

    def test_dividend_score_scales_with_yield(self):
        none_yield = category_scores({}, financial_score=0, disclosure_score_raw=0, dividend_yield=0, atr20_pct=3)
        high_yield = category_scores({}, financial_score=0, disclosure_score_raw=0, dividend_yield=5, atr20_pct=3)
        self.assertEqual(0.0, none_yield["dividend_score"])
        self.assertEqual(100.0, high_yield["dividend_score"])

    def test_momentum_score_rewards_strength_volume_and_trend(self):
        weak = momentum_score(relative_strength_score=-5, volume_ratio=0, trend_score=0)
        strong = momentum_score(relative_strength_score=5, volume_ratio=3, trend_score=30)
        self.assertEqual(0.0, weak)
        self.assertEqual(100.0, strong)


if __name__ == "__main__":
    unittest.main()
