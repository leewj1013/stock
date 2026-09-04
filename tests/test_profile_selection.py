import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.app import CandidateEvaluation, Pick, profile_total_score, recommend_for_profiles, select_for_profile


def _evaluation(ticker, score=70, atr20_pct=3, **categories):
    values = {
        "profitability_score": 50.0, "growth_score": 50.0, "stability_score": 50.0,
        "dividend_score": 50.0, "momentum_score": 50.0, "news_category_score": 50.0,
        **categories,
    }
    pick = Pick(ticker, ticker, 100, 1.0, 5_000_000_000, score, atr20_pct=atr20_pct)
    return CandidateEvaluation(ticker, ticker, values, pick)


class ProfileTotalScoreTest(unittest.TestCase):
    def test_weighted_sum_of_category_values(self):
        values = {"profitability_score": 80.0, "growth_score": 20.0}
        weights = {"profitability": 0.5, "growth": 0.5}
        self.assertEqual(50.0, profile_total_score(values, weights))

    def test_missing_category_value_defaults_to_neutral_fifty(self):
        weights = {"profitability": 1.0}
        self.assertEqual(50.0, profile_total_score({}, weights))


class SelectForProfileTest(unittest.TestCase):
    @patch("stock_alarm.app.open_recommended_tickers", return_value=set())
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    def test_different_weights_produce_different_ranking(self, _state, _blocked):
        stable = _evaluation("A", stability_score=90.0, momentum_score=10.0)
        momentum = _evaluation("B", stability_score=10.0, momentum_score=90.0)
        stability_profile = {"scoring_weights": {"stability": 1.0}, "max_volatility_atr_pct": None, "max_holdings": None}
        momentum_profile = {"scoring_weights": {"momentum": 1.0}, "max_volatility_atr_pct": None, "max_holdings": None}

        stability_pick = select_for_profile([stable, momentum], stability_profile, top_n=1)
        momentum_pick = select_for_profile([stable, momentum], momentum_profile, top_n=1)

        self.assertEqual("A", stability_pick[0].ticker)
        self.assertEqual("B", momentum_pick[0].ticker)

    @patch("stock_alarm.app.open_recommended_tickers", return_value=set())
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    def test_max_volatility_excludes_high_atr_candidates(self, _state, _blocked):
        calm = _evaluation("A", atr20_pct=2)
        volatile = _evaluation("B", atr20_pct=8)
        profile = {"scoring_weights": None, "max_volatility_atr_pct": 5.0, "max_holdings": None}

        result = select_for_profile([calm, volatile], profile, top_n=5)

        self.assertEqual(["A"], [pick.ticker for pick in result])

    @patch("stock_alarm.app.open_recommended_tickers", return_value=set())
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": [{"ticker": "X"}, {"ticker": "Y"}]})
    def test_max_holdings_leaves_room_for_only_remaining_slots(self, _state, _blocked):
        picks = [_evaluation(ticker, score=90 - index) for index, ticker in enumerate(("A", "B", "C"))]
        profile = {"scoring_weights": None, "max_volatility_atr_pct": None, "max_holdings": 3}

        result = select_for_profile(picks, profile, top_n=5)

        self.assertEqual(1, len(result))
        self.assertEqual("A", result[0].ticker)

    @patch("stock_alarm.app.open_recommended_tickers", return_value={"A"})
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    def test_already_recommended_ticker_is_blocked(self, _state, _blocked):
        picks = [_evaluation("A"), _evaluation("B")]
        profile = {"scoring_weights": None, "max_volatility_atr_pct": None, "max_holdings": None}

        result = select_for_profile(picks, profile, top_n=5)

        self.assertEqual(["B"], [pick.ticker for pick in result])


class RecommendForProfilesTest(unittest.TestCase):
    @patch("stock_alarm.data_store.write_profile_selections")
    @patch("stock_alarm.data_store.write_candidates")
    @patch("stock_alarm.app.open_recommended_tickers", return_value=set())
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"holdings": []})
    @patch("stock_alarm.app.market_benchmark_return", return_value=("KOSPI", 0.0))
    @patch("stock_alarm.app.apply_relative_strength", side_effect=lambda evaluations, _benchmark: evaluations)
    @patch("stock_alarm.app.evaluate_naver_candidate")
    @patch("stock_alarm.app.passes_market_filter", return_value=True)
    @patch("stock_alarm.app.recommend_universe", return_value={"A": "A", "B": "B"})
    def test_profiles_select_different_tickers_from_the_shared_evaluation(
        self, _universe, _filter, evaluate, _relative, _benchmark, _state, _blocked, write_candidates, write_selections,
    ):
        evaluate.side_effect = lambda ticker, *args, **kwargs: _evaluation_with_score(ticker, stability_score=90.0 if ticker == "A" else 10.0,
                                                                                        momentum_score=10.0 if ticker == "A" else 90.0)
        with patch.dict("stock_alarm.trading_profiles.PROFILES", {
            "aggressive": {"db_path": "data/stock_alarm.db", "scoring_weights": {"momentum": 1.0}, "max_volatility_atr_pct": None, "max_holdings": None},
            "neutral": {"db_path": "data/stock_alarm_neutral.db", "scoring_weights": {"stability": 1.0}, "max_volatility_atr_pct": None, "max_holdings": None},
        }, clear=True):
            picks_by_profile = recommend_for_profiles(date(2026, 9, 4), top_n=1, min_trading_value=0, volume_multiplier=1.5, run_id="run-1")

        self.assertEqual(["B"], [pick.ticker for pick in picks_by_profile["aggressive"]])
        self.assertEqual(["A"], [pick.ticker for pick in picks_by_profile["neutral"]])
        write_candidates.assert_called_once()
        self.assertEqual(2, write_selections.call_count)


def _evaluation_with_score(ticker, **categories):
    values = {
        "profitability_score": 50.0, "growth_score": 50.0, "stability_score": 50.0,
        "dividend_score": 50.0, "momentum_score": 50.0, "news_category_score": 50.0,
        **categories,
    }
    pick = Pick(ticker, ticker, 100, 1.0, 5_000_000_000, 70)
    return CandidateEvaluation(ticker, ticker, values, pick)


if __name__ == "__main__":
    unittest.main()
