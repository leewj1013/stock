import os
import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.app import (
    Pick,
    market_exposure_limit_pct,
    market_up_ratio,
    naver_market_up_ratio,
    passes_market_filter,
    recommend_naver,
)


class MarketFilterTest(unittest.TestCase):
    def test_live_exposure_limit_thresholds(self):
        self.assertEqual(70.0, market_exposure_limit_pct(0.60))
        self.assertEqual(40.0, market_exposure_limit_pct(0.45))
        self.assertEqual(10.0, market_exposure_limit_pct(0.4499))

    def test_market_up_ratio(self):
        self.assertEqual(0.5, market_up_ratio([True, False]))

    @patch("stock_alarm.market_breadth.cached_whole_market_up_ratio", return_value=0.8)
    def test_naver_market_up_ratio_prefers_whole_market_breadth(self, _cached):
        self.assertEqual(0.8, naver_market_up_ratio(date(2026, 7, 25)))

    @patch("stock_alarm.app.watchlist_market_up_ratio", return_value=0.3)
    @patch("stock_alarm.market_breadth.cached_whole_market_up_ratio", return_value=None)
    def test_naver_market_up_ratio_falls_back_to_watchlist(self, _cached, watchlist):
        self.assertEqual(0.3, naver_market_up_ratio(date(2026, 7, 25)))
        watchlist.assert_called_once()

    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.44)
    def test_rejects_weak_market(self, _ratio):
        old = os.environ.get("MIN_MARKET_UP_RATIO")
        os.environ["MIN_MARKET_UP_RATIO"] = "0.45"
        try:
            self.assertFalse(passes_market_filter(date(2026, 7, 25)))
        finally:
            if old is None:
                os.environ.pop("MIN_MARKET_UP_RATIO", None)
            else:
                os.environ["MIN_MARKET_UP_RATIO"] = old

    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.44)
    def test_market_filter_is_active_by_default(self, _ratio):
        old = os.environ.get("MIN_MARKET_UP_RATIO")
        os.environ.pop("MIN_MARKET_UP_RATIO", None)
        try:
            self.assertFalse(passes_market_filter(date(2026, 7, 25)))
        finally:
            if old is not None:
                os.environ["MIN_MARKET_UP_RATIO"] = old

    @patch("stock_alarm.app.configured_stocks", return_value={"A": "A"})
    @patch("stock_alarm.app.passes_market_filter", return_value=False)
    @patch("stock_alarm.app.make_naver_pick", return_value=Pick("A", "A", 1, 1, 1, 1))
    def test_recommend_naver_returns_empty_on_weak_market(self, make_pick, _filter, _stocks):
        self.assertEqual([], recommend_naver(date(2026, 7, 25), 5, 0, 1.5))
        make_pick.assert_not_called()


if __name__ == "__main__":
    unittest.main()
