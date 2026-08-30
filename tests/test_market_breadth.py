import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.market_breadth import (
    cached_whole_market_up_ratio,
    fetch_market_cap_page_moves,
    whole_market_up_ratio,
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
    def test_fetch_market_cap_page_moves_reads_change_pct_sign(self):
        with patch("stock_alarm.market_breadth.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = SAMPLE_PAGE.encode("euc-kr")
            moves = fetch_market_cap_page_moves("KOSPI", 1)

        self.assertEqual([False, True], moves)

    def test_whole_market_up_ratio_averages_both_markets(self):
        with patch("stock_alarm.market_breadth.fetch_market_cap_page_moves", return_value=[True, False]):
            self.assertEqual(0.5, whole_market_up_ratio(pages_per_market=1))

    def test_whole_market_up_ratio_returns_none_when_scrape_fails(self):
        with patch("stock_alarm.market_breadth.fetch_market_cap_page_moves", side_effect=Exception("network")):
            self.assertIsNone(whole_market_up_ratio(pages_per_market=1))

    def test_cached_whole_market_up_ratio_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "latest.json")
            with open(cache_path, "w", encoding="utf-8") as file:
                json.dump({"ratio": 0.75}, file)
            with patch("stock_alarm.market_breadth.CACHE_PATH", cache_path), \
                 patch("stock_alarm.market_breadth.whole_market_up_ratio") as whole:
                self.assertEqual(0.75, cached_whole_market_up_ratio())
                whole.assert_not_called()


if __name__ == "__main__":
    unittest.main()
