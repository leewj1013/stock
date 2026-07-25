import unittest
from unittest.mock import patch

import json
import os
import tempfile

from stock_alarm.news_reference import cache_path, extract_titles, keyword_score, news_titles


class NewsReferenceTest(unittest.TestCase):
    def test_keyword_score(self):
        score, notes = keyword_score(["호실적 수주 증가", "악재 하락"])

        self.assertEqual(1, score)
        self.assertIn("good=3", notes)
        self.assertIn("bad=2", notes)

    def test_cache_path(self):
        self.assertIn(".cache", cache_path("삼성전자"))

    def test_extract_titles_reads_old_naver_markup(self):
        self.assertEqual(["삼성전자 호실적 발표"], extract_titles('<a class="news_tit" title="삼성전자 호실적 발표"></a>'))

    def test_extract_titles_reads_news_links(self):
        html = '<a href="https://n.news.naver.com/article/1"><span>SK텔레콤 수주 증가</span></a>'

        self.assertEqual(["SK텔레콤 수주 증가"], extract_titles(html))

    @patch("stock_alarm.news_reference.cache_path")
    def test_news_titles_reads_cache(self, path):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as file:
            json.dump(["cached"], file)
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        path.return_value = file.name

        self.assertEqual(["cached"], news_titles("x"))


if __name__ == "__main__":
    unittest.main()
