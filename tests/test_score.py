import unittest
from unittest.mock import patch

from stock_alarm.app import calculate_score, news_bonus


class ScoreTest(unittest.TestCase):
    def test_score_is_capped_at_100(self):
        self.assertEqual(100, calculate_score(120, 100, 10, 1_000_000_000_000))

    def test_score_rewards_balanced_signal(self):
        weak = calculate_score(101, 100, 1.5, 10_000_000_000)
        strong = calculate_score(108, 100, 2.5, 200_000_000_000)

        self.assertGreater(strong, weak)

    @patch.dict("os.environ", {}, clear=True)
    def test_news_bonus_is_off_by_default(self):
        self.assertEqual(0, news_bonus("Samsung"))

    @patch.dict("os.environ", {"NEWS_SCORE_WEIGHT": "2"})
    @patch("stock_alarm.news_reference.reference", return_value=("3", "news=3"))
    def test_news_bonus_uses_weight(self, _reference):
        self.assertEqual(6, news_bonus("Samsung"))


if __name__ == "__main__":
    unittest.main()
