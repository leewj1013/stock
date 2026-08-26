import unittest
import os
import tempfile
from unittest.mock import patch

from datetime import date

from stock_alarm.app import CandidateEvaluation, Pick, apply_relative_strength, calculate_score, calculate_score_parts, dart_bonus, market_benchmark_return, news_bonus, performance_penalty, write_log


class ScoreTest(unittest.TestCase):
    def test_extended_price_does_not_get_max_trend_score(self):
        self.assertLess(calculate_score(120, 100, 10, 1_000_000_000_000), 100)

    def test_score_rewards_balanced_signal(self):
        weak = calculate_score(101, 100, 1.5, 10_000_000_000)
        strong = calculate_score(108, 100, 2.5, 200_000_000_000)

        self.assertGreater(strong, weak)

    def test_score_prefers_moderate_ma20_distance_over_chasing(self):
        moderate = calculate_score(102, 100, 2, 300_000_000_000, 2)
        extended = calculate_score(115, 100, 2, 300_000_000_000, 2)
        self.assertGreater(moderate, extended)

    def test_score_parts_match_total(self):
        parts = calculate_score_parts(108, 100, 2.5, 200_000_000_000)

        self.assertEqual(calculate_score(108, 100, 2.5, 200_000_000_000), round(sum(parts), 2))

    def test_relative_strength_compares_with_watchlist_proxy(self):
        strong = CandidateEvaluation("A", "A", {"day_return_pct": 4, "final_score": 70}, Pick("A", "A", 100, 2, 1, 70))
        weak = CandidateEvaluation("B", "B", {"day_return_pct": -2, "final_score": 70}, Pick("B", "B", 100, 2, 1, 70))
        adjusted = apply_relative_strength([strong, weak])
        self.assertGreater(adjusted[0].pick.score, adjusted[1].pick.score)
        self.assertEqual(1.0, adjusted[0].values["market_proxy_return_pct"])

    @patch("stock_alarm.app.naver_rows", return_value=[[20260806, 0, 0, 0, 100, 1], [20260807, 0, 0, 0, 102, 1]])
    def test_market_benchmark_uses_kospi(self, _rows):
        self.assertEqual(("KOSPI", 2.0), market_benchmark_return(date(2026, 8, 7)))

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
                file.write("ticker,return_1d_pct,return_3d_pct,return_5d_pct\n")
                file.writelines("000001,-4,-4,-4\n" for _ in range(20))
                file.writelines("000002,1,1,1\n" for _ in range(20))

            self.assertEqual(2, performance_penalty("000001", path))
            self.assertEqual(0, performance_penalty("000002", path))


if __name__ == "__main__":
    unittest.main()
