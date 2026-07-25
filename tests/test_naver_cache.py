import os
import unittest
from datetime import date

from stock_alarm.app import naver_cache_path


class NaverCacheTest(unittest.TestCase):
    def test_cache_path_contains_ticker_and_dates(self):
        path = naver_cache_path("005930", date(2024, 1, 1), date(2024, 1, 31))

        self.assertTrue(path.endswith(os.path.join(".cache", "naver", "005930-20240101-20240131.json")))


if __name__ == "__main__":
    unittest.main()
