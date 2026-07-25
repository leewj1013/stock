import unittest
import os
import tempfile
from unittest.mock import patch

from stock_alarm.app import Pick, calculate_score, calculate_score_parts, dart_bonus, news_bonus, performance_penalty, write_log


class ScoreTest(unittest.TestCase):
    def test_score_is_capped_at_100(self):
        self.assertEqual(100, calculate_score(120, 100, 10, 1_000_000_000_000))

    def test_score_rewards_balanced_signal(self):
        weak = calculate_score(101, 100, 1.5, 10_000_000_000)
        strong = calculate_score(108, 100, 2.5, 200_000_000_000)

        self.assertGreater(strong, weak)

    def test_score_parts_match_total(self):
        parts = calculate_score_parts(108, 100, 2.5, 200_000_000_000)

        self.assertEqual(calculate_score(108, 100, 2.5, 200_000_000_000), round(sum(parts), 2))

    def test_write_log_includes_score_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "recommendations.csv")
            write_log([Pick("000001", "A", 1000, 2, 100_000_000_000, 70, 30, 20, 20)], path)

            with open(path, encoding="utf-8-sig") as file:
                text = file.read()

            self.assertIn("volume_score,trading_value_score,trend_score", text)
            self.assertIn("30.00,20.00,20.00", text)

    @patch.dict("os.environ", {}, clear=True)
    def test_news_bonus_is_off_by_default(self):
        self.assertEqual(0, news_bonus("Samsung"))

    @patch.dict("os.environ", {"NEWS_SCORE_WEIGHT": "2"})
    @patch("stock_alarm.news_reference.reference", return_value=("3", "news=3"))
    def test_news_bonus_uses_weight(self, _reference):
        self.assertEqual(6, news_bonus("Samsung"))

    @patch.dict("os.environ", {}, clear=True)
    def test_dart_bonus_is_off_by_default(self):
        self.assertEqual(0, dart_bonus("005930"))

    @patch.dict("os.environ", {"DART_SCORE_WEIGHT": "2"})
    @patch("stock_alarm.dart_reference.reference", return_value=("3", "dart=3"))
    def test_dart_bonus_uses_weight(self, _reference):
        self.assertEqual(6, dart_bonus("005930"))

    def test_performance_penalty_needs_enough_bad_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "performance.csv")
            with open(path, "w", encoding="utf-8-sig") as file:
                file.write("ticker,return_1d_pct\n000001,-2\n000001,-4\n000002,-9\n")

            self.assertEqual(0, performance_penalty("000001", path))
            self.assertEqual(0, performance_penalty("000002", path))

    def test_performance_penalty_uses_bad_average(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "performance.csv")
            with open(path, "w", encoding="utf-8-sig") as file:
                file.write("ticker,return_1d_pct\n000001,-2\n000001,-4\n000001,-6\n000002,3\n000002,-1\n000002,1\n")

            self.assertEqual(4, performance_penalty("000001", path))
            self.assertEqual(0, performance_penalty("000002", path))


if __name__ == "__main__":
    unittest.main()
