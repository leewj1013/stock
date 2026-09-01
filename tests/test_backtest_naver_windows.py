import unittest
from datetime import date, timedelta
from unittest.mock import patch

from stock_alarm.backtest import naver_close_after, naver_next_open


class BacktestNaverWindowsTest(unittest.TestCase):
    """naver_close_after/naver_next_open cache their naver_rows(...) result
    forever (max_cache_age_seconds is not set). If the requested end date is
    still in the future, that locks in whatever partial history existed the
    first time this exact (ticker, start, end) range was queried -- even
    after real trading days accumulate past hold_days. Capping the query's
    end date to today keeps the cache key moving until the window is actually
    complete, instead of freezing it on a still-filling range.
    """

    @patch("stock_alarm.backtest.naver_rows")
    def test_naver_close_after_caps_end_day_to_today_when_window_is_still_future(self, naver_rows):
        naver_rows.return_value = []
        today = date.today()
        start_day = today - timedelta(days=5)

        naver_close_after("005930", start_day, 20)

        called_end = naver_rows.call_args.args[2]
        self.assertEqual(today, called_end)

    @patch("stock_alarm.backtest.naver_rows")
    def test_naver_close_after_uses_full_window_once_it_is_in_the_past(self, naver_rows):
        naver_rows.return_value = []
        start_day = date.today() - timedelta(days=365)

        naver_close_after("005930", start_day, 20)

        called_end = naver_rows.call_args.args[2]
        self.assertEqual(start_day + timedelta(days=60), called_end)

    @patch("stock_alarm.backtest.naver_rows")
    def test_naver_next_open_caps_end_day_to_today(self, naver_rows):
        naver_rows.return_value = []
        today = date.today()

        naver_next_open("005930", today - timedelta(days=1))

        called_end = naver_rows.call_args.args[2]
        self.assertEqual(today, called_end)


if __name__ == "__main__":
    unittest.main()
