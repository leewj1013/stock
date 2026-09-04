import unittest

from stock_alarm.profile_weight_learning import CATEGORIES, _proposed_weights, _ranked_returns, learn_profile_weights


def _row(value, **categories):
    base = {category: 50.0 for category in CATEGORIES}
    base.update(categories)
    return (base, value)


class ProfileWeightLearningTest(unittest.TestCase):
    def test_proposed_weights_favor_the_category_that_correlates_with_returns(self):
        current = {category: 1 / len(CATEGORIES) for category in CATEGORIES}
        training = [
            ({**{c: 50.0 for c in CATEGORIES}, "growth": 90.0}, 5.0),
            ({**{c: 50.0 for c in CATEGORIES}, "growth": 10.0}, -5.0),
            ({**{c: 50.0 for c in CATEGORIES}, "growth": 95.0}, 4.0),
            ({**{c: 50.0 for c in CATEGORIES}, "growth": 5.0}, -4.0),
        ]

        proposed = _proposed_weights(training, current)

        self.assertGreater(proposed["growth"], current["growth"])
        self.assertAlmostEqual(1.0, sum(proposed.values()), places=2)

    def test_ranked_returns_keeps_only_the_top_half_by_weighted_score(self):
        weights = {category: (1.0 if category == "growth" else 0.0) for category in CATEGORIES}
        validation = [({**{c: 0.0 for c in CATEGORIES}, "growth": 90.0}, 5.0), ({**{c: 0.0 for c in CATEGORIES}, "growth": 10.0}, -5.0)]

        self.assertEqual([5.0], _ranked_returns(validation, weights, top_fraction=0.5))

    def test_learn_profile_weights_reports_insufficient_data_below_the_minimum(self):
        current = {category: 1 / len(CATEGORIES) for category in CATEGORIES}
        rows = [("2024-01-01", {c: 50.0 for c in CATEGORIES}, 1.0) for _ in range(5)]

        result = learn_profile_weights(rows, current, fold_count=2, validation_size=60)

        self.assertEqual("insufficient_data", result["status"])
        self.assertEqual(5, result["sample_count"])


if __name__ == "__main__":
    unittest.main()
