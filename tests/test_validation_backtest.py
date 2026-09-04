import unittest
from unittest.mock import patch

from stock_alarm.app import CandidateEvaluation, Pick
from stock_alarm.validation_backtest import BacktestEngine


def _bare_engine(profile=None, top_n=5, minimum_score=50):
    engine = BacktestEngine.__new__(BacktestEngine)
    engine.profile = profile
    engine.top_n = top_n
    engine.minimum_score = minimum_score
    return engine


def _evaluation(ticker, score=70, atr20_pct=3, **categories):
    values = {
        "profitability_score": 50.0, "growth_score": 50.0, "stability_score": 50.0,
        "dividend_score": 50.0, "momentum_score": 50.0, "news_category_score": 50.0,
        "atr20_pct": atr20_pct,
        **categories,
    }
    pick = Pick(ticker, ticker, 100, 1.0, 5_000_000_000, score, atr20_pct=atr20_pct)
    return CandidateEvaluation(ticker, ticker, values, pick)


class BacktestEngineCategoryTrainingRowsTest(unittest.TestCase):
    @patch.object(BacktestEngine, "forward_outcomes")
    @patch.object(BacktestEngine, "passed_evaluations")
    def test_collects_category_scores_and_forward_excess_return_per_passing_candidate(self, passed, forward_outcomes):
        engine = _bare_engine(minimum_score=50)
        engine.regimes = {"20260101": "bull"}
        engine.by_date = {"KOSPI": {"20260101": (0, [])}}
        passed.return_value = [_evaluation("A", score=70, growth_score=80.0)]
        forward_outcomes.return_value = {"excess_5d_pct": 3.5}

        rows = engine.category_training_rows()

        self.assertEqual(1, len(rows))
        day, categories, value = rows[0]
        self.assertEqual("20260101", day)
        self.assertEqual(80.0, categories["growth"])
        self.assertEqual(3.5, value)

    @patch.object(BacktestEngine, "forward_outcomes")
    @patch.object(BacktestEngine, "passed_evaluations")
    def test_skips_candidates_below_the_minimum_score_or_missing_forward_outcome(self, passed, forward_outcomes):
        engine = _bare_engine(minimum_score=50)
        engine.regimes = {"20260101": "bull"}
        engine.by_date = {"KOSPI": {"20260101": (0, [])}}
        passed.return_value = [_evaluation("A", score=30)]
        forward_outcomes.return_value = {}

        self.assertEqual([], engine.category_training_rows())


class BacktestEngineCandidatesTest(unittest.TestCase):
    @patch.object(BacktestEngine, "passed_evaluations")
    def test_max_holdings_leaves_room_for_only_remaining_slots(self, passed):
        engine = _bare_engine(profile={"max_holdings": 3, "scoring_weights": None, "max_volatility_atr_pct": None})
        passed.return_value = [_evaluation(ticker, score=90 - index) for index, ticker in enumerate(("A", "B", "C"))]

        result = engine.candidates("20260904", set(), open_count=2)

        self.assertEqual(["A"], [row["ticker"] for row in result])

    @patch.object(BacktestEngine, "passed_evaluations")
    def test_max_volatility_excludes_high_atr_candidates(self, passed):
        engine = _bare_engine(profile={"max_holdings": None, "scoring_weights": None, "max_volatility_atr_pct": 5.0})
        passed.return_value = [_evaluation("A", atr20_pct=2), _evaluation("B", atr20_pct=8)]

        result = engine.candidates("20260904", set())

        self.assertEqual(["A"], [row["ticker"] for row in result])

    @patch.object(BacktestEngine, "passed_evaluations")
    def test_no_profile_ranks_by_the_existing_factor_score(self, passed):
        engine = _bare_engine(profile=None)
        passed.return_value = [_evaluation("A", score=60), _evaluation("B", score=90)]

        result = engine.candidates("20260904", set())

        self.assertEqual(["B", "A"], [row["ticker"] for row in result])


if __name__ == "__main__":
    unittest.main()
