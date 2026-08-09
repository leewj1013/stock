import csv
import os
import tempfile
import unittest
from unittest.mock import patch

from datetime import date

from stock_alarm.recommendation_performance import (
    EXTERNAL_COLUMNS,
    external_reference,
    lines,
    performance_rows,
    next_execution,
    pick_trading_day,
    read_recommendations,
    score_adjustment_suggestion,
    score_bucket_summary,
    suggested_min_score,
)


class RecommendationPerformanceTest(unittest.TestCase):
    def test_read_recommendations(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["created_at", "ticker", "name", "close", "score"])
            writer.writerow(["2026-07-25T16:10:00", "005930", "Samsung", "100", "80"])
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))

        self.assertEqual("005930", read_recommendations(file.name)[0]["ticker"])

    @patch.dict(os.environ, {}, clear=True)
    def test_external_reference_is_off_by_default(self):
        self.assertEqual(("", "", ""), external_reference("Samsung", "005930"))

    @patch.dict(os.environ, {"NEWS_LOOKUP": "1"})
    @patch("stock_alarm.recommendation_performance.news_reference", return_value=("2", "news=3 good=2 bad=0"))
    def test_external_reference_uses_news_when_enabled(self, _news):
        self.assertEqual(("2", "", "news=3 good=2 bad=0"), external_reference("Samsung", "005930"))

    @patch.dict(os.environ, {"DART_LOOKUP": "1"})
    @patch("stock_alarm.recommendation_performance.dart_reference", return_value=("1", "dart=2 good=1 bad=0"))
    def test_external_reference_uses_dart_when_enabled(self, _dart):
        self.assertEqual(("", "1", "dart=2 good=1 bad=0"), external_reference("Samsung", "005930"))

    @patch("stock_alarm.recommendation_performance.price_excursions", return_value=("25.00", "-5.00"))
    @patch("stock_alarm.recommendation_performance.naver_close_after", side_effect=[110, 120, None, 130, 140])
    @patch("stock_alarm.recommendation_performance.next_execution", return_value=(date(2026, 7, 25), 100))
    @patch("stock_alarm.recommendation_performance.pick_trading_day", return_value=date(2026, 7, 24))
    def test_performance_rows(self, _day, _execution, _close, _excursions):
        rows = performance_rows([{"created_at": "2026-07-25T16:10:00", "ticker": "005930", "name": "Samsung", "close": "100", "score": "80"}])

        self.assertEqual("9.70", rows[0][5])
        self.assertEqual("19.70", rows[0][6])
        self.assertEqual("", rows[0][7])
        self.assertEqual("29.70", rows[0][8])
        self.assertEqual("39.70", rows[0][9])
        self.assertEqual("25.00", rows[0][10])
        self.assertEqual("-5.00", rows[0][11])
        self.assertEqual(["", "", "", "legacy row: external signals unavailable at recommendation time"], rows[0][12:16])
        self.assertEqual(["100", "2026-07-25", "30"], rows[0][16:])
        self.assertEqual(["news_score", "disclosure_score", "financial_score", "external_notes"], EXTERNAL_COLUMNS)

    @patch("stock_alarm.recommendation_performance.naver_rows", return_value=[[20260724, 0, 0, 0, 100, 1]])
    def test_pick_trading_day_uses_matching_close(self, _rows):
        self.assertEqual(date(2026, 7, 24), pick_trading_day("005930", date(2026, 7, 25), 100))

    @patch("stock_alarm.recommendation_performance.naver_rows", return_value=[[20260724, 90, 0, 0, 100, 1], [20260727, 105, 0, 0, 110, 1]])
    def test_next_execution_uses_next_session_open(self, _rows):
        self.assertEqual((date(2026, 7, 27), 105), next_execution("005930", date(2026, 7, 24)))

    def test_lines(self):
        text = "\n".join(lines([["2026-07-25", "005930", "Samsung", "80", "100", "10.00", "", ""]]))

        self.assertIn("completed_1d=1", text)
        self.assertIn("avg_1d_return_pct=10.00", text)

    def test_score_bucket_summary(self):
        rows = [
            ["2026-07-25", "A", "A", "95", "100", "10.00", "", ""],
            ["2026-07-25", "B", "B", "80", "100", "-5.00", "", ""],
            ["2026-07-25", "C", "C", "60", "100", "1.00", "", ""],
        ]
        text = dict(score_bucket_summary(rows))

        self.assertEqual("1", text["score_90_plus_completed_1d"])
        self.assertEqual("-5.00", text["score_70_89_avg_1d_return_pct"])
        self.assertEqual("100.0", text["score_under_70_win_rate_1d_pct"])

    def test_score_adjustment_suggestion_waits_for_data(self):
        self.assertIn("not enough data", score_adjustment_suggestion([]))

    def test_score_adjustment_suggestion_names_best_and_worst(self):
        rows = [["2026-07-25", str(index), "X", "95", "100", "1.00", "", ""] for index in range(10)]
        rows.extend([["2026-07-25", str(index), "X", "80", "100", "-1.00", "", ""] for index in range(10, 20)])

        self.assertEqual("watch score_90_plus higher, score_70_89 lower", score_adjustment_suggestion(rows))

    def test_suggested_min_score_uses_positive_buckets(self):
        rows = [["2026-07-25", str(index), "X", "95", "100", "1.00", "", ""] for index in range(10)]
        rows.extend([["2026-07-25", str(index), "X", "80", "100", "-1.00", "", ""] for index in range(10, 20)])

        self.assertEqual("90", suggested_min_score(rows))

    @patch("stock_alarm.recommendation_performance.price_excursions", return_value=("", ""))
    @patch("stock_alarm.recommendation_performance.naver_close_after", return_value=None)
    @patch("stock_alarm.recommendation_performance.next_execution", return_value=(date(2026, 7, 25), 100))
    @patch("stock_alarm.recommendation_performance.pick_trading_day", return_value=date(2026, 7, 24))
    def test_performance_rows_dedupes_by_pick_day_and_ticker(self, _day, _execution, _close, _excursions):
        rows = performance_rows(
            [
                {"created_at": "2026-07-25T01:00:00", "ticker": "005930", "name": "Samsung", "close": "100", "score": "80"},
                {"created_at": "2026-07-25T02:00:00", "ticker": "005930", "name": "Samsung", "close": "100", "score": "80"},
            ]
        )

        self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()
