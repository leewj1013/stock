import os
import unittest
from unittest.mock import mock_open, patch

from stock_alarm.app import configured_stocks


class ConfiguredStocksTest(unittest.TestCase):
    def test_parses_stock_env(self):
        old = os.environ.get("STOCKS")
        os.environ["STOCKS"] = "005930:Samsung,035720:Kakao"
        try:
            self.assertEqual({"005930": "Samsung", "035720": "Kakao"}, configured_stocks())
        finally:
            if old is None:
                os.environ.pop("STOCKS", None)
            else:
                os.environ["STOCKS"] = old

    @patch("os.path.exists", return_value=True)
    def test_reads_watchlist_csv_when_stock_env_is_empty(self, _exists):
        old = os.environ.get("STOCKS")
        os.environ["STOCKS"] = ""
        data = "ticker,name\n005930,Samsung\n"
        try:
            with patch("builtins.open", mock_open(read_data=data)):
                self.assertEqual({"005930": "Samsung"}, configured_stocks())
        finally:
            if old is None:
                os.environ.pop("STOCKS", None)
            else:
                os.environ["STOCKS"] = old


if __name__ == "__main__":
    unittest.main()
