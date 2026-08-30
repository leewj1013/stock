from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request


CACHE_PATH = os.path.join(".cache", "market_breadth", "latest.json")


def _clean(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def fetch_market_cap_page_moves(market: str, page: int) -> list[bool]:
    """Up/down moves for one page of Naver's KOSPI/KOSDAQ market-cap ranking."""
    sosok = "0" if market == "KOSPI" else "1"
    url = "https://finance.naver.com/sise/sise_market_sum.naver?" + urllib.parse.urlencode({"sosok": sosok, "page": page})
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 stockAlarm"})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read()
    text = None
    for encoding in ("euc-kr", "cp949", "utf-8"):
        try:
            text = body.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return []
    moves = []
    for table_row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        if "code=" not in table_row:
            continue
        cells = [_clean(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", table_row, flags=re.I | re.S)]
        if len(cells) < 5:
            continue
        try:
            moves.append(float(cells[4].replace("%", "").replace(",", "")) > 0)
        except ValueError:
            continue
    return moves


def whole_market_up_ratio(pages_per_market: int = 6) -> float | None:
    """Advance/decline ratio sampled from the top market-cap pages of both markets.

    Full KOSPI+KOSDAQ has ~2600 tickers; scraping every page on a 5-minute
    cron would be slow and rude to Naver, so this samples the largest-cap
    pages from each market as a practical whole-market breadth proxy.
    """
    moves: list[bool] = []
    for market in ("KOSPI", "KOSDAQ"):
        for page in range(1, pages_per_market + 1):
            try:
                moves.extend(fetch_market_cap_page_moves(market, page))
            except Exception:
                continue
    return (sum(moves) / len(moves)) if moves else None


def cached_whole_market_up_ratio(max_cache_age_seconds: int = 600, pages_per_market: int = 6) -> float | None:
    if os.environ.get("NO_CACHE", "0") != "1" and os.path.exists(CACHE_PATH):
        if time.time() - os.path.getmtime(CACHE_PATH) <= max_cache_age_seconds:
            with open(CACHE_PATH, encoding="utf-8") as file:
                return json.load(file).get("ratio")
    ratio = whole_market_up_ratio(pages_per_market)
    if ratio is not None:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as file:
            json.dump({"ratio": ratio}, file)
    return ratio
