import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.app import Pick, open_recommended_tickers, recommend_for_day, top_picks


class RecommendSortTest(unittest.TestCase):
    @patch("stock_alarm.app.make_pick")
    def test_returns_top_scored_picks(self, make_pick):
        make_pick.side_effect = [
            Pick("A", "A", 100, 2, 5_000_000_000, 51),
            Pick("B", "B", 100, 2, 5_000_000_000, 90),
        ]

        picks = recommend_for_day(date(2026, 1, 2), ["KOSPI"], 1, 0, 2, lambda *_args, **_kwargs: ["A", "B"])

        self.assertEqual("B", picks[0].ticker)

    @patch.dict("os.environ", {"MIN_RECOMMEND_SCORE": "5"})
    def test_top_picks_filters_min_score(self):
        picks = top_picks(
            [
                Pick("A", "A", 100, 2, 5_000_000_000, 1),
                Pick("B", "B", 100, 2, 5_000_000_000, 9),
            ],
            5,
        )

        self.assertEqual(["B"], [pick.ticker for pick in picks])

    @patch.dict("os.environ", {}, clear=True)
    def test_top_picks_defaults_to_score_50(self):
        picks = top_picks(
            [
                Pick("A", "A", 100, 2, 5_000_000_000, 49),
                Pick("B", "B", 100, 2, 5_000_000_000, 50),
            ],
            5,
        )

        self.assertEqual(["B"], [pick.ticker for pick in picks])

    @patch("stock_alarm.app.open_recommended_tickers", return_value={"A"})
    def test_top_picks_skips_open_recommendations(self, _open_tickers):
        picks = top_picks(
            [
                Pick("A", "A", 100, 2, 5_000_000_000, 90),
                Pick("B", "B", 100, 2, 5_000_000_000, 80),
            ],
            5,
        )

        self.assertEqual(["B"], [pick.ticker for pick in picks])

    def test_sell_alert_allows_recommendation_again(self):
        with self.subTest("positions minus sell alerts"):
            import csv
            import os
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                positions = os.path.join(directory, "positions.csv")
                alerts = os.path.join(directory, "sell_alerts.csv")
                recommendations = os.path.join(directory, "recommendations.csv")
                with open(positions, "w", newline="", encoding="utf-8-sig") as file:
                    writer = csv.writer(file)
                    writer.writerow(["ticker", "name", "entry_price", "entry_date"])
                    writer.writerow(["A", "A", "100", "2026-07-27"])
                    writer.writerow(["B", "B", "100", "2026-07-27"])
                with open(alerts, "w", newline="", encoding="utf-8-sig") as file:
                    writer = csv.writer(file)
                    writer.writerow(["created_at", "ticker"])
                    writer.writerow(["2026-07-27T15:00:00", "A"])

                self.assertEqual({"B"}, open_recommended_tickers(positions, alerts, recommendations))

    def test_new_position_after_sell_alert_blocks_recommendation_again(self):
        import csv
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            positions = os.path.join(directory, "positions.csv")
            alerts = os.path.join(directory, "sell_alerts.csv")
            recommendations = os.path.join(directory, "recommendations.csv")
            with open(positions, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["ticker", "name", "entry_price", "entry_date"])
                writer.writerow(["A", "A", "100", "2026-07-27"])
                writer.writerow(["A", "A", "110", "2026-07-29"])
            with open(alerts, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["created_at", "ticker"])
                writer.writerow(["2026-07-28T15:00:00", "A"])

            self.assertEqual({"A"}, open_recommended_tickers(positions, alerts, recommendations))

    def test_recommendation_after_sell_alert_blocks_recommendation_again(self):
        import csv
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            positions = os.path.join(directory, "positions.csv")
            alerts = os.path.join(directory, "sell_alerts.csv")
            recommendations = os.path.join(directory, "recommendations.csv")
            with open(positions, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["ticker", "name", "entry_price", "entry_date"])
                writer.writerow(["A", "A", "100", "2026-07-29"])
            with open(alerts, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["created_at", "ticker"])
                writer.writerow(["2026-07-29T13:55:00", "A"])
            with open(recommendations, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(["created_at", "ticker"])
                writer.writerow(["2026-07-29T14:05:00", "A"])

            self.assertEqual({"A"}, open_recommended_tickers(positions, alerts, recommendations))


if __name__ == "__main__":
    unittest.main()
