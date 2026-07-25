from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date


GOOD_WORDS = ["호실적", "수주", "증가", "상승", "개선", "흑자", "성장", "최대"]
BAD_WORDS = ["악재", "하락", "감소", "적자", "소송", "리콜", "부진", "급락"]


def cache_path(query: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", query).strip("_") or "query"
    return os.path.join(".cache", "news", f"{date.today().isoformat()}_{safe}.json")


def news_titles(query: str, limit: int = 10) -> list[str]:
    path = cache_path(query)
    if os.environ.get("NO_CACHE", "0") != "1" and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            return json.load(file)[:limit]
    url = "https://search.naver.com/search.naver?" + urllib.parse.urlencode({"where": "news", "query": query})
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode("utf-8", errors="ignore")
    result = extract_titles(text, limit)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False)
    return result


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
    noise = ("언론사", "새 창", "구독", "프로필")
    return "" if len(title) < 8 or any(word in title for word in noise) else title


def keyword_score(titles: list[str]) -> tuple[int, str]:
    text = " ".join(titles)
    good = sum(text.count(word) for word in GOOD_WORDS)
    bad = sum(text.count(word) for word in BAD_WORDS)
    return good - bad, f"news={len(titles)} good={good} bad={bad}"


def reference(query: str) -> tuple[str, str]:
    score, notes = keyword_score(news_titles(query))
    return str(score), notes
