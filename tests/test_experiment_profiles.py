import unittest

from stock_alarm.trading_profiles import PROFILES

STORAGE_KEYS = {"db_path", "sell_alerts_log", "notify", "comparison_only"}
RULE_KEYS = {"sell_policy", "risk_release"}


def without(profile, keys):
    return {key: value for key, value in profile.items() if key not in keys}


class ExperimentProfilesTest(unittest.TestCase):
    def test_control_copies_aggressive_except_its_own_storage(self):
        control = PROFILES["exp_control"]
        self.assertEqual(without(PROFILES["aggressive"], STORAGE_KEYS), without(control, STORAGE_KEYS))
        self.assertFalse(control["notify"])
        self.assertTrue(control["comparison_only"])

    def test_candidate_differs_from_control_only_in_exit_and_release_rules(self):
        candidate = PROFILES["exp_candidate"]
        self.assertEqual(without(PROFILES["exp_control"], STORAGE_KEYS | RULE_KEYS), without(candidate, STORAGE_KEYS | RULE_KEYS))
        self.assertEqual({"disable_take_profit_in_regimes": ["bull"]}, candidate["sell_policy"])
        self.assertEqual({"mode": "cooldown", "cooldown_days": 20, "reentry_scale": 0.3}, candidate["risk_release"])

    def test_every_profile_has_its_own_database_and_alert_log(self):
        for key in ("db_path", "sell_alerts_log"):
            values = [profile[key] for profile in PROFILES.values()]
            self.assertEqual(len(values), len(set(values)))


if __name__ == "__main__":
    unittest.main()
