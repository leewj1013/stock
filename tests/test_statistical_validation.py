import unittest

from stock_alarm.statistical_validation import benjamini_hochberg, newey_west_mean_test


class StatisticalValidationTest(unittest.TestCase):
    def test_newey_west_lag_zero_matches_known_mean_standard_error(self):
        result = newey_west_mean_test([1.0, 2.0, 3.0, 4.0], lag=0)
        self.assertAlmostEqual(2.5, result["mean"], places=10)
        self.assertAlmostEqual(0.5590169943749475, result["standard_error"], places=10)
        self.assertAlmostEqual(4.47213595499958, result["t_statistic"], places=10)
        # Student t(df=3), |t|=4.472135955 has two-sided p=0.0208351512.
        self.assertAlmostEqual(0.020835151196185, result["p_value"], places=10)

    def test_newey_west_zero_mean_has_unit_p_value(self):
        result = newey_west_mean_test([1.0, -1.0, 1.0, -1.0], lag=1)
        self.assertEqual(0.0, result["mean"])
        self.assertEqual(1.0, result["p_value"])

    def test_benjamini_hochberg_known_values_and_original_order(self):
        adjusted = benjamini_hochberg([0.01, 0.04, 0.03, 0.002, None])
        expected = [0.02, 0.04, 0.04, 0.008, None]
        for actual, wanted in zip(adjusted, expected):
            if wanted is None:
                self.assertIsNone(actual)
            else:
                self.assertAlmostEqual(wanted, actual, places=12)


if __name__ == "__main__":
    unittest.main()
