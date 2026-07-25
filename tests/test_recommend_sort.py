import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.app import Pick, recommend_for_day, top_picks


class RecommendSortTest(unittest.TestCase):
    @patch("stock_alarm.app.make_pick")
    def test_returns_top_scored_picks(self, make_pick):
        make_pick.side_effect = [
            Pick("A", "A", 100, 2, 5_000_000_000, 1),
            Pick("B", "B", 100, 2, 5_000_000_000, 9),
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


if __name__ == "__main__":
    unittest.main()
