import os
import unittest
from unittest.mock import mock_open, patch

from stock_alarm.app import configured_stocks, recommend_universe


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


    @patch.dict(os.environ, {"DYNAMIC_SCREENING_TOP_N": "0"})
    def test_recommend_universe_matches_watchlist_when_screening_disabled(self):
        self.assertEqual(configured_stocks(), recommend_universe(5_000_000_000))

    @patch("stock_alarm.market_breadth.krx_top_trading_value_candidates", return_value={"999999": "새로운후보"})
    @patch.dict(os.environ, {"DYNAMIC_SCREENING_TOP_N": "30"})
    def test_recommend_universe_adds_new_dynamic_candidates(self, _krx):
        universe = recommend_universe(5_000_000_000)

        self.assertEqual("새로운후보", universe["999999"])
        for ticker, name in configured_stocks().items():
            self.assertEqual(name, universe[ticker])

    def test_recommend_universe_does_not_override_existing_watchlist_name(self):
        watchlist_ticker, watchlist_name = next(iter(configured_stocks().items()))
        with patch("stock_alarm.market_breadth.krx_top_trading_value_candidates", return_value={watchlist_ticker: "다른이름"}), \
             patch.dict(os.environ, {"DYNAMIC_SCREENING_TOP_N": "30"}):
            universe = recommend_universe(5_000_000_000)

        self.assertEqual(watchlist_name, universe[watchlist_ticker])

    @patch("stock_alarm.market_breadth.krx_top_trading_value_candidates", side_effect=RuntimeError("network"))
    @patch.dict(os.environ, {"DYNAMIC_SCREENING_TOP_N": "30"})
    def test_recommend_universe_falls_back_to_watchlist_when_screening_fails(self, _krx):
        self.assertEqual(configured_stocks(), recommend_universe(5_000_000_000))


if __name__ == "__main__":
    unittest.main()
