import copy
import unittest

from stock_alarm.strategy_learning import FACTORS
from stock_alarm.weight_variant_backtest import (
    EXTERNAL_FACTORS,
    effective_points,
    load_variant_config,
    validate_variant_config,
)


class WeightVariantBacktestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_variant_config()

    def test_every_variant_defines_all_factors_and_preserves_external_weights(self):
        baseline = self.config["variants"]["variant_1"]["weights"]
        for variant in self.config["variants"].values():
            self.assertEqual(set(FACTORS), set(variant["weights"]))
            for factor in EXTERNAL_FACTORS:
                self.assertEqual(baseline[factor], variant["weights"][factor])

    def test_variant_2_redistributes_removed_trend_proportionally(self):
        points = effective_points(
            self.config["base_factor_points"], self.config["variants"]["variant_2"]["weights"]
        )
        self.assertAlmostEqual(100.0, points["volume_score"] + points["trading_value_score"] + points["trend_score"], places=5)
        self.assertAlmostEqual(4 / 3, points["volume_score"] / points["trading_value_score"], places=5)
        self.assertEqual(0.0, points["trend_score"])

    def test_variant_3_core_points_sum_to_original_capacity(self):
        points = effective_points(
            self.config["base_factor_points"], self.config["variants"]["variant_3"]["weights"]
        )
        self.assertAlmostEqual(100.0, sum(points[factor] for factor in ("volume_score", "trading_value_score", "trend_score")), places=5)
        self.assertAlmostEqual(2.5, points["relative_strength_score"], places=5)

    def test_variant_4_ic_allocation_preserves_technical_capacity(self):
        points = effective_points(
            self.config["base_factor_points"], self.config["variants"]["variant_4"]["weights"]
        )
        self.assertAlmostEqual(105.0, sum(points[factor] for factor in ("volume_score", "trading_value_score", "trend_score", "relative_strength_score")), places=4)

    def test_validator_rejects_external_factor_change(self):
        changed = copy.deepcopy(self.config)
        changed["variants"]["variant_2"]["weights"]["news_score"] = 0.5
        with self.assertRaisesRegex(ValueError, "protected external factor"):
            validate_variant_config(changed)


if __name__ == "__main__":
    unittest.main()
