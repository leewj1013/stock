import unittest
from datetime import datetime, timedelta

from stock_alarm.risk_release_policy import ExperimentalRiskController, load_risk_release_variants


class RiskReleasePolicyTest(unittest.TestCase):
    def test_experiment_config_defines_all_requested_policy_families(self):
        variants = load_risk_release_variants()
        self.assertEqual(
            {"baseline", "rebound", "cooldown", "hysteresis", "reduced"},
            {row["mode"] for row in variants.values()},
        )

    def test_monthly_peak_decay_reduces_only_high_water_and_can_release(self):
        controller = ExperimentalRiskController({
            "name": "variant_F_monthly_1", "mode": "peak_decay",
            "decay_frequency": "monthly", "decay_pct": 1,
        })
        first = controller.evaluate(
            {"total_equity": 89_000, "holdings_value": 0}, 89_000, 89_000, 100_000,
            datetime(2026, 1, 15),
        )
        self.assertEqual("halted", first["status"])
        second = controller.evaluate(
            {"total_equity": 89_000, "holdings_value": 0}, 89_000, 89_000, 100_000,
            datetime(2026, 2, 15),
        )
        self.assertAlmostEqual(99_000, second["effective_high_water"])
        released = controller.evaluate(
            {"total_equity": 89_000, "holdings_value": 0}, 89_000, 89_000, 99_000,
            datetime(2026, 3, 15),
        )
        self.assertEqual("active", released["status"])
        self.assertAlmostEqual(98_010, released["effective_high_water"])

    def test_peak_decay_does_not_disable_exposure_hard_stop(self):
        controller = ExperimentalRiskController({
            "name": "variant_F_quarterly_5", "mode": "peak_decay",
            "decay_frequency": "quarterly", "decay_pct": 5,
        })
        result = controller.evaluate(
            {"total_equity": 100_000, "holdings_value": 80_000}, 100_000, 100_000, 100_000,
            datetime(2026, 1, 15),
        )
        self.assertEqual("halted", result["status"])
        self.assertIn("exposure_limit", result["reason"])

    def evaluate(self, controller, equity, day=0, holdings=0):
        return controller.evaluate(
            {"total_equity": equity, "holdings_value": holdings},
            daily_start_equity=equity, weekly_start_equity=equity,
            high_water=100_000,
            created_at=datetime(2026, 1, 1) + timedelta(days=day),
        )

    def test_variant_a_remains_locked_below_existing_drawdown_boundary(self):
        controller = ExperimentalRiskController({"name": "variant_A", "mode": "baseline"})
        self.assertEqual("halted", self.evaluate(controller, 89_000)["status"])
        for day in range(1, 100):
            result = self.evaluate(controller, 89_000, day)
        self.assertEqual("halted", result["status"])

    def test_variant_b_releases_after_low_rebound(self):
        controller = ExperimentalRiskController({"name": "variant_B_5", "mode": "rebound", "rebound_pct": 5})
        self.assertEqual("halted", self.evaluate(controller, 89_000)["status"])
        self.assertEqual("halted", self.evaluate(controller, 84_000, 1)["status"])
        released = self.evaluate(controller, 88_200, 2)
        self.assertEqual("active", released["status"])
        self.assertEqual("rebound_released", released["episode_transition"])

    def test_variant_b_10_requires_larger_rebound(self):
        controller = ExperimentalRiskController({"name": "variant_B_10", "mode": "rebound", "rebound_pct": 10})
        self.evaluate(controller, 84_000)
        self.assertEqual("halted", self.evaluate(controller, 90_000, 1)["status"])
        self.assertEqual("active", self.evaluate(controller, 92_400, 2)["status"])

    def test_variant_c_releases_reduced_size_after_cooldown(self):
        controller = ExperimentalRiskController({
            "name": "variant_C_20_30", "mode": "cooldown", "cooldown_days": 20, "reentry_scale": 0.3,
        })
        for day in range(19):
            self.assertEqual("halted", self.evaluate(controller, 89_000, day)["status"])
        released = self.evaluate(controller, 89_000, 19)
        self.assertEqual("reduced", released["status"])
        self.assertEqual(0.3, released["allocation_scale"])

    def test_variant_d_requires_drawdown_to_recover_inside_minus_five(self):
        controller = ExperimentalRiskController({
            "name": "variant_D", "mode": "hysteresis", "release_drawdown_pct": 5,
        })
        self.assertEqual("halted", self.evaluate(controller, 89_000)["status"])
        self.assertEqual("halted", self.evaluate(controller, 92_000, 1)["status"])
        self.assertEqual("active", self.evaluate(controller, 96_000, 2)["status"])

    def test_variant_e_keeps_reduced_entry_but_hard_exposure_stop_still_wins(self):
        controller = ExperimentalRiskController({"name": "variant_E_3", "mode": "reduced", "reentry_scale": 0.3})
        reduced = self.evaluate(controller, 89_000)
        self.assertEqual("reduced", reduced["status"])
        self.assertEqual(0.3, reduced["allocation_scale"])
        hard_stop = self.evaluate(controller, 100_000, 1, holdings=80_000)
        self.assertEqual("halted", hard_stop["status"])
        self.assertIn("exposure_limit", hard_stop["reason"])


if __name__ == "__main__":
    unittest.main()
