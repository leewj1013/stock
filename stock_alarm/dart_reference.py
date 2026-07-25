from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from io import BytesIO


GOOD_WORDS = ["실적", "매출", "영업이익", "흑자", "수주", "증가"]
BAD_WORDS = ["적자", "감소", "손상", "소송", "부진"]


def corp_code_by_stock(ticker: str) -> str:
    path = os.path.join(".cache", "dart", "corp_codes.json")
    codes = load_corp_codes(path) if os.path.exists(path) else fetch_corp_codes(path)
    return codes.get(ticker, "")


def fetch_corp_codes(path: str) -> dict[str, str]:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        return {}
    url = "https://opendart.fss.or.kr/api/corpCode.xml?" + urllib.parse.urlencode({"crtfc_key": key})
    with urllib.request.urlopen(url, timeout=20) as response:
        body = response.read()
    with zipfile.ZipFile(BytesIO(body)) as archive:
        xml = archive.read(archive.namelist()[0])
    root = ET.fromstring(xml)
    codes = {
        item.findtext("stock_code", "").strip(): item.findtext("corp_code", "").strip()
        for item in root.findall("list")
        if item.findtext("stock_code", "").strip()
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(codes, file, ensure_ascii=False)
    return codes


def load_corp_codes(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def disclosure_titles(ticker: str, days: int = 30, limit: int = 10) -> list[str]:
    key = os.environ.get("DART_API_KEY", "")
    corp_code = corp_code_by_stock(ticker)
    if not key or not corp_code:
        return []
    end = date.today()
    start = end - timedelta(days=days)
    url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode(
        {
            "crtfc_key": key,
            "corp_code": corp_code,
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_count": limit,
        }
    )
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    return [item.get("report_nm", "") for item in data.get("list", []) if item.get("report_nm")]


def keyword_score(titles: list[str]) -> tuple[int, str]:
    text = " ".join(re.sub(r"\s+", " ", title) for title in titles)
    good = sum(text.count(word) for word in GOOD_WORDS)
    bad = sum(text.count(word) for word in BAD_WORDS)
    return good - bad, f"dart={len(titles)} good={good} bad={bad}"


def reference(ticker: str) -> tuple[str, str]:
    score, notes = keyword_score(disclosure_titles(ticker))
    return str(score), notes
