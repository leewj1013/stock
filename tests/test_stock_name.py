import os
import unittest
from unittest.mock import Mock, patch

from stock_alarm.app import stock_name


class StockNameTest(unittest.TestCase):
    def setUp(self):
        stock_name.cache_clear()

    def test_can_disable_korean_name_lookup(self):
        old = os.environ.get("KOREAN_STOCK_NAMES")
        os.environ["KOREAN_STOCK_NAMES"] = "0"
        try:
            self.assertEqual("Samsung Electronics", stock_name("005930", "Samsung Electronics"))
        finally:
            if old is None:
                os.environ.pop("KOREAN_STOCK_NAMES", None)
            else:
                os.environ["KOREAN_STOCK_NAMES"] = old

    @patch.dict("sys.modules", {"pykrx": Mock(stock=Mock(get_market_ticker_name=Mock(return_value="삼성전자")))})
    def test_uses_pykrx_name_when_available(self):
        old = os.environ.get("KOREAN_STOCK_NAMES")
        os.environ["KOREAN_STOCK_NAMES"] = "1"
        try:
            self.assertEqual("삼성전자", stock_name("005930", "Samsung Electronics"))
        finally:
            if old is None:
                os.environ.pop("KOREAN_STOCK_NAMES", None)
            else:
                os.environ["KOREAN_STOCK_NAMES"] = old


if __name__ == "__main__":
    unittest.main()
