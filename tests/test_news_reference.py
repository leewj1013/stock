import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.news_reference import article_score, cache_path, extract_titles, keyword_score, naver_api_articles, naver_api_titles, news_titles


class NewsReferenceTest(unittest.TestCase):
    def test_keyword_score(self):
        score, notes = keyword_score(["수주", "증가", "개선", "악재", "하락"])

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

    def test_extract_titles_skips_noise(self):
        html = '<a class="news_tit" title="언론사 구독 뉴스홈"></a><a class="news_tit" title="삼성전자 실적 개선 기대"></a>'

        self.assertEqual(["삼성전자 실적 개선 기대"], extract_titles(html))

    @patch("stock_alarm.news_reference.cache_path")
    def test_news_titles_reads_cache(self, path):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as file:
            json.dump(["cached"], file)
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        path.return_value = file.name

        self.assertEqual(["cached"], news_titles("x"))

    @patch("stock_alarm.news_reference.urllib.request.urlopen")
    def test_naver_api_titles_uses_official_json_api(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({"items": [{"title": "<b>삼성전자</b> 실적 개선 기대"}]}).encode()

        result = naver_api_titles("삼성전자", "client", "secret")

        self.assertEqual(["삼성전자 실적 개선 기대"], result)
        request = urlopen.call_args.args[0]
        self.assertEqual("client", request.headers["X-ncp-apigw-api-key-id"])
        self.assertEqual("secret", request.headers["X-ncp-apigw-api-key"])

    @patch("stock_alarm.news_reference.urllib.request.urlopen")
    def test_naver_api_articles_keeps_description_and_domain(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({"items": [{
            "title": "<b>삼성전자</b> 실적 개선 기대",
            "description": "<b>삼성전자</b>가 이번 분기 수주 증가로...",
            "originallink": "https://www.hankyung.com/article/1",
        }]}).encode()

        result = naver_api_articles("삼성전자", "client", "secret")

        self.assertEqual([{
            "title": "삼성전자 실적 개선 기대",
            "description": "삼성전자 가 이번 분기 수주 증가로...",
            "domain": "hankyung.com",
        }], result)

    def test_article_score_weights_untrusted_source_lower(self):
        trusted = [{"title": "실적 개선", "description": "", "domain": "hankyung.com"}]
        untrusted = [{"title": "실적 개선", "description": "", "domain": "random-blog.tistory.com"}]

        trusted_score, _ = article_score(trusted)
        untrusted_score, _ = article_score(untrusted)

        self.assertEqual(1.0, trusted_score)
        self.assertEqual(0.6, untrusted_score)

    def test_article_score_reads_description_too(self):
        articles = [{"title": "특징주 살펴보기", "description": "이번 수주 증가로 주목받고 있다", "domain": "hankyung.com"}]

        score, notes = article_score(articles)

        self.assertEqual(1.0, score)
        self.assertIn("good=1.0", notes)


if __name__ == "__main__":
    unittest.main()
