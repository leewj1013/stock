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

# Major wire services and general/economic newspapers. Anything else is not
# excluded, just weighted down so a single obscure blog can't swing the score
# as much as a wire-service report.
TRUSTED_DOMAINS = {
    "yna.co.kr", "yonhapnewstv.co.kr", "hankyung.com", "mk.co.kr", "sedaily.com",
    "edaily.co.kr", "joongang.co.kr", "joins.com", "chosun.com", "donga.com",
    "hani.co.kr", "khan.co.kr", "seoul.co.kr", "mt.co.kr", "fnnews.com",
    "asiae.co.kr", "news1.kr", "newsis.com", "kmib.co.kr", "etnews.com",
    "ytn.co.kr", "kbs.co.kr", "sbs.co.kr", "imbc.com", "hankookilbo.com",
}
TRUSTED_SOURCE_WEIGHT = 1.0
OTHER_SOURCE_WEIGHT = 0.6


def cache_path(query: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", query).strip("_") or "query"
    return os.path.join(".cache", "news", f"{date.today().isoformat()}_{safe}.json")


def news_titles(query: str, limit: int = 10) -> list[str]:
    path = cache_path(query)
    if os.environ.get("NO_CACHE", "0") != "1" and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            return json.load(file)[:limit]
    client_id = os.environ.get("NAVER_HUB_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_HUB_CLIENT_SECRET", "")
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
    url = "https://naverapihub.apigw.ntruss.com/search/v1/news?" + urllib.parse.urlencode({"query": query, "display": min(max(limit, 1), 100), "sort": "date"})
    request = urllib.request.Request(url, headers={"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": client_secret, "User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    return [title for title in (clean_title(re.sub(r"<[^>]+>", " ", item.get("title", ""))) for item in body.get("items", [])) if title][:limit]


def _domain(url: str) -> str:
    netloc = urllib.parse.urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _is_trusted_domain(domain: str) -> bool:
    return any(domain == trusted or domain.endswith(f".{trusted}") for trusted in TRUSTED_DOMAINS)


def naver_api_articles(query: str, client_id: str, client_secret: str, limit: int = 10) -> list[dict]:
    """Like naver_api_titles but keeps the description and source domain for
    context/reliability scoring instead of just the bare title."""
    url = "https://naverapihub.apigw.ntruss.com/search/v1/news?" + urllib.parse.urlencode({"query": query, "display": min(max(limit, 1), 100), "sort": "date"})
    request = urllib.request.Request(url, headers={"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": client_secret, "User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    articles = []
    for item in body.get("items", []):
        title = clean_title(re.sub(r"<[^>]+>", " ", item.get("title", "")))
        if not title:
            continue
        description = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", item.get("description", "")))).strip()
        articles.append({"title": title, "description": description, "domain": _domain(item.get("originallink") or item.get("link") or "")})
        if len(articles) >= limit:
            break
    return articles


def news_articles(query: str, limit: int = 10) -> list[dict]:
    """Article-level counterpart to news_titles(), with description and source domain.

    The HTML fallback path has no description/domain, so those come back empty
    for those results but the title is still usable for keyword scoring.
    """
    path = cache_path(query).replace(".json", "_articles.json")
    if os.environ.get("NO_CACHE", "0") != "1" and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            return json.load(file)[:limit]
    client_id = os.environ.get("NAVER_HUB_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_HUB_CLIENT_SECRET", "")
    if os.environ.get("NAVER_OFFICIAL_NEWS_API", "0") == "1" and client_id and client_secret:
        try:
            result = naver_api_articles(query, client_id, client_secret, limit)
        except (HTTPError, URLError, OSError, ValueError, KeyError):
            result = [{"title": title, "description": "", "domain": ""} for title in naver_html_titles(query, limit)]
    else:
        result = [{"title": title, "description": "", "domain": ""} for title in naver_html_titles(query, limit)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False)
    return result


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


def article_score(articles: list[dict]) -> tuple[float, str]:
    """keyword_score() but scans title+description and discounts untrusted sources."""
    good = bad = 0.0
    trusted_count = 0
    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')}"
        trusted = _is_trusted_domain(article.get("domain", ""))
        trusted_count += trusted
        weight = TRUSTED_SOURCE_WEIGHT if trusted else OTHER_SOURCE_WEIGHT
        if any(word in text for word in GOOD_WORDS):
            good += weight
        if any(word in text for word in BAD_WORDS):
            bad += weight
    return round(good - bad, 2), f"news={len(articles)} good={good:.1f} bad={bad:.1f} trusted={trusted_count}"


def reference(query: str) -> tuple[str, str]:
    score, notes = article_score(news_articles(query))
    return str(score), notes
