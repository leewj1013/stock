import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.market_breadth import (
    cached_whole_market_average_change_pct,
    cached_whole_market_up_ratio,
    fetch_krx_market_changes,
    fetch_krx_market_moves,
    fetch_market_cap_page_changes,
    fetch_market_cap_page_moves,
    krx_top_trading_value_candidates,
    whole_market_snapshot,
    whole_market_up_ratio,
    whole_market_up_ratio_via_krx,
)


SAMPLE_PAGE = """
<table><tr>
<td>1</td><td><a href="/item/main.naver?code=005930">삼성전자</a></td>
<td>257,000</td><td>하락<br>9,000</td><td>-3.38%</td><td>100</td><td>15,024,936</td>
</tr><tr>
<td>2</td><td><a href="/item/main.naver?code=000660">SK하이닉스</a></td>
<td>1,900,000</td><td>상승<br>50,000</td><td>2.70%</td><td>5,000</td><td>12,075,039</td>
</tr></table>
"""


class MarketBreadthTest(unittest.TestCase):
    def test_fetch_market_cap_page_changes_reads_change_pct(self):
        with patch("stock_alarm.market_breadth.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = SAMPLE_PAGE.encode("euc-kr")
            changes = fetch_market_cap_page_changes("KOSPI", 1)

        self.assertEqual([-3.38, 2.70], changes)

    def test_fetch_market_cap_page_moves_derives_sign_from_changes(self):
        with patch("stock_alarm.market_breadth.fetch_market_cap_page_changes", return_value=[-3.38, 2.70]):
            self.assertEqual([False, True], fetch_market_cap_page_moves("KOSPI", 1))

    def test_whole_market_snapshot_averages_both_markets_from_naver(self):
        with patch("stock_alarm.market_breadth.whole_market_up_ratio_via_krx", return_value=None), \
             patch("stock_alarm.market_breadth._whole_market_snapshot_via_krx", return_value=None), \
             patch("stock_alarm.market_breadth.fetch_market_cap_page_changes", return_value=[2.0, -4.0]):
            snapshot = whole_market_snapshot(pages_per_market=1)
            ratio = whole_market_up_ratio(pages_per_market=1)

        self.assertEqual(0.5, snapshot["up_ratio"])
        self.assertEqual(-1.0, snapshot["avg_change_pct"])
        self.assertEqual(0.5, ratio)

    def test_whole_market_snapshot_returns_none_when_scrape_fails(self):
        with patch("stock_alarm.market_breadth._whole_market_snapshot_via_krx", return_value=None), \
             patch("stock_alarm.market_breadth.fetch_market_cap_page_changes", side_effect=Exception("network")):
            self.assertIsNone(whole_market_snapshot(pages_per_market=1))

    def test_whole_market_snapshot_prefers_krx_over_naver_scrape(self):
        with patch("stock_alarm.market_breadth._whole_market_snapshot_via_krx", return_value={"up_ratio": 0.8, "avg_change_pct": 1.2}), \
             patch("stock_alarm.market_breadth.fetch_market_cap_page_changes") as scrape:
            snapshot = whole_market_snapshot()

        self.assertEqual({"up_ratio": 0.8, "avg_change_pct": 1.2}, snapshot)
        scrape.assert_not_called()

    def test_fetch_krx_market_changes_reads_fluctuation_pct(self):
        with patch.dict("os.environ", {"KRX_API_KEY": "key"}), \
             patch("stock_alarm.market_breadth.urllib.request.urlopen") as urlopen:
            payload = {"OutBlock_1": [{"FLUC_RT": "-3.38"}, {"FLUC_RT": "2.70"}]}
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
            changes = fetch_krx_market_changes("KOSPI", "20260828")

        self.assertEqual([-3.38, 2.70], changes)

    def test_fetch_krx_market_moves_derives_sign_from_changes(self):
        with patch("stock_alarm.market_breadth.fetch_krx_market_changes", return_value=[-3.38, 2.70]):
            self.assertEqual([False, True], fetch_krx_market_moves("KOSPI", "20260828"))

    def test_fetch_krx_market_changes_returns_empty_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual([], fetch_krx_market_changes("KOSPI", "20260828"))

    def test_whole_market_up_ratio_via_krx_averages_both_markets(self):
        with patch("stock_alarm.app.latest_naver_trading_day") as trading_day, \
             patch("stock_alarm.market_breadth.fetch_krx_market_rows", return_value=[{"FLUC_RT": "1.0"}, {"FLUC_RT": "2.0"}, {"FLUC_RT": "-1.0"}]):
            trading_day.return_value.strftime.return_value = "20260828"
            self.assertAlmostEqual(2 / 3, whole_market_up_ratio_via_krx())

    def test_krx_top_trading_value_candidates_filters_and_ranks(self):
        rows = [
            {"ISU_CD": "005930", "ISU_NM": "삼성전자", "ACC_TRDVAL": "500000000000"},
            {"ISU_CD": "005935", "ISU_NM": "삼성전자우", "ACC_TRDVAL": "900000000000"},
            {"ISU_CD": "000660", "ISU_NM": "SK하이닉스", "ACC_TRDVAL": "300000000000"},
            {"ISU_CD": "000001", "ISU_NM": "너무작은거래", "ACC_TRDVAL": "1000000000"},
        ]
        with patch("stock_alarm.app.latest_naver_trading_day") as trading_day, \
             patch("stock_alarm.market_breadth.fetch_krx_market_rows", return_value=rows):
            trading_day.return_value.strftime.return_value = "20260828"
            result = krx_top_trading_value_candidates(top_n=1, min_trading_value=5_000_000_000)

        self.assertEqual({"005930": "삼성전자"}, result)

    def test_cached_functions_reuse_fresh_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "latest.json")
            with open(cache_path, "w", encoding="utf-8") as file:
                json.dump({"up_ratio": 0.75, "avg_change_pct": 1.5}, file)
            with patch("stock_alarm.market_breadth.CACHE_PATH", cache_path), \
                 patch("stock_alarm.market_breadth.whole_market_snapshot") as whole:
                self.assertEqual(0.75, cached_whole_market_up_ratio())
                self.assertEqual(1.5, cached_whole_market_average_change_pct())
                whole.assert_not_called()

    def test_cached_functions_recompute_when_cache_is_old_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "latest.json")
            with open(cache_path, "w", encoding="utf-8") as file:
                json.dump({"ratio": 0.75}, file)
            with patch("stock_alarm.market_breadth.CACHE_PATH", cache_path), \
                 patch("stock_alarm.market_breadth.whole_market_snapshot", return_value={"up_ratio": 0.6, "avg_change_pct": 0.3}):
                self.assertEqual(0.6, cached_whole_market_up_ratio())


if __name__ == "__main__":
    unittest.main()
