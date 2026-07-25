import unittest

from stock_alarm.app import calculate_score


class ScoreTest(unittest.TestCase):
    def test_score_is_capped_at_100(self):
        self.assertEqual(100, calculate_score(120, 100, 10, 1_000_000_000_000))

    def test_score_rewards_balanced_signal(self):
        weak = calculate_score(101, 100, 1.5, 10_000_000_000)
        strong = calculate_score(108, 100, 2.5, 200_000_000_000)

        self.assertGreater(strong, weak)


if __name__ == "__main__":
    unittest.main()
