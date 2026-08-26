from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from datetime import date


GOOD_WORDS = ["호실적", "수주", "증가", "상승", "개선", "흑자", "성장", "최대", "실적", "계약"]
BAD_WORDS = ["악재", "하락", "감소", "적자", "소송", "리콜", "부진", "급락", "손실", "하향"]


def cache_path(query: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", query).strip("_") or "query"
    return os.path.join(".cache", "news", f"{date.today().isoformat()}_{safe}.json")


def news_titles(query: str, limit: int = 10) -> list[str]:
    path = cache_path(query)
    if os.environ.get("NO_CACHE", "0") != "1" and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            return json.load(file)[:limit]
    client_id = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    if os.environ.get("NAVER_OFFICIAL_NEWS_API", "0") == "1" and client_id and client_secret:
        try:
            result = naver_api_titles(query, client_id, client_secret, limit)
        except (HTTPError, URLError, OSError, ValueError, KeyError):
            result = naver_html_titles(query, limit)
    else:
        result = naver_html_titles(query, limit)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False)
    return result


def naver_api_titles(query: str, client_id: str, client_secret: str, limit: int = 10) -> list[str]:
    url = "https://openapi.naver.com/v1/search/news.json?" + urllib.parse.urlencode({"query": query, "display": min(max(limit, 1), 100), "sort": "date"})
    request = urllib.request.Request(url, headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret, "User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    return [title for title in (clean_title(re.sub(r"<[^>]+>", " ", item.get("title", ""))) for item in body.get("items", [])) if title][:limit]


def naver_html_titles(query: str, limit: int = 10) -> list[str]:
    url = "https://search.naver.com/search.naver?" + urllib.parse.urlencode({"where": "news", "query": query})
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode("utf-8", errors="ignore")
    return extract_titles(text, limit)


def extract_titles(text: str, limit: int = 10) -> list[str]:
    titles = [clean_title(title) for title in re.findall(r'class="news_tit"[^>]*title="([^"]+)"', text)]
    for href, body in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        if "news" not in href:
            continue
        title = clean_title(re.sub(r"<[^>]+>", " ", body))
        if title:
            titles.append(title)
    result = []
    for title in titles:
        if title and title not in result:
            result.append(title)
        if len(result) >= limit:
            break
    return result


def clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", html.unescape(value)).strip()
    noise = ("언론사", "구독", "포토", "뉴스홈")
    return "" if len(title) < 8 or any(word in title for word in noise) else title


def keyword_score(titles: list[str]) -> tuple[int, str]:
    good = sum(any(word in title for word in GOOD_WORDS) for title in titles)
    bad = sum(any(word in title for word in BAD_WORDS) for title in titles)
    return good - bad, f"news={len(titles)} good={good} bad={bad}"


def reference(query: str) -> tuple[str, str]:
    score, notes = keyword_score(news_titles(query))
    return str(score), notes
