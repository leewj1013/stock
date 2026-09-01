from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from statistics import mean


CACHE_PATH = os.path.join(".cache", "market_breadth", "latest.json")
KRX_TRADE_INFO_URL = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
}


def fetch_krx_market_rows(market: str, bas_dd: str) -> list[dict]:
    """Raw per-stock daily trade rows for one market on one date, via the
    official KRX Open API (data.krx.co.kr) -- the full market, not a sample."""
    key = os.environ.get("KRX_API_KEY", "")
    if not key:
        return []
    url = KRX_TRADE_INFO_URL[market] + "?" + urllib.parse.urlencode({"basDd": bas_dd})
    request = urllib.request.Request(url, headers={"AUTH_KEY": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body.get("OutBlock_1") or []


def fetch_krx_market_changes(market: str, bas_dd: str) -> list[float]:
    """Per-stock %-change for every listed stock in one market on one date."""
    changes = []
    for row in fetch_krx_market_rows(market, bas_dd):
        try:
            changes.append(float(row.get("FLUC_RT", 0)))
        except (TypeError, ValueError):
            continue
    return changes


def fetch_krx_market_moves(market: str, bas_dd: str) -> list[bool]:
    return [change > 0 for change in fetch_krx_market_changes(market, bas_dd)]


def _latest_krx_market_rows() -> list[dict]:
    """KRX's official daily-trade-info API only has a session's numbers once
    that session is finalized after close, so during market hours the latest
    Naver-reported trading day (today, mid-session) is still empty here. Walk
    back a few calendar days to the most recent date KRX has actually published.
    """
    from .app import latest_naver_trading_day

    day = latest_naver_trading_day()
    for _ in range(5):
        bas_dd = day.strftime("%Y%m%d")
        rows: list[dict] = []
        for market in ("KOSPI", "KOSDAQ"):
            try:
                rows.extend(fetch_krx_market_rows(market, bas_dd))
            except Exception:
                continue
        if rows:
            return rows
        day -= timedelta(days=1)
    return []


def _whole_market_snapshot_via_krx() -> dict[str, float] | None:
    changes = []
    for row in _latest_krx_market_rows():
        try:
            changes.append(float(row.get("FLUC_RT", 0)))
        except (TypeError, ValueError):
            continue
    if not changes:
        return None
    return {"up_ratio": sum(value > 0 for value in changes) / len(changes), "avg_change_pct": mean(changes)}


def _looks_like_preferred_share(name: str) -> bool:
    return bool(re.search(r"\d?우(B)?$", name))


def krx_top_trading_value_rows(top_n: int = 30, min_trading_value: int = 0) -> list[dict]:
    """Today's (or the latest finalized session's) top-N stocks by trading
    value across the whole KOSPI+KOSDAQ market, with ticker/name/%-change."""
    candidates = []
    for row in _latest_krx_market_rows():
        ticker = str(row.get("ISU_CD") or "").strip()
        name = str(row.get("ISU_NM") or "").strip()
        if not ticker or not name or _looks_like_preferred_share(name):
            continue
        try:
            trading_value = int(float(row.get("ACC_TRDVAL") or 0))
            change_pct = float(row.get("FLUC_RT") or 0)
        except (TypeError, ValueError):
            continue
        if trading_value < min_trading_value:
            continue
        candidates.append({"ticker": ticker, "name": name, "trading_value": trading_value, "change_pct": change_pct})
    candidates.sort(key=lambda row: row["trading_value"], reverse=True)
    return candidates[:top_n]


def krx_top_trading_value_candidates(top_n: int = 30, min_trading_value: int = 0) -> dict[str, str]:
    """Meant to widen the recommend universe past the static watchlist --
    every candidate here still has to clear the same liquidity/technical/
    quality filters as a watchlist ticker."""
    return {row["ticker"]: row["name"] for row in krx_top_trading_value_rows(top_n, min_trading_value)}


def whole_market_up_ratio_via_krx() -> float | None:
    snapshot = _whole_market_snapshot_via_krx()
    return snapshot["up_ratio"] if snapshot else None


def _clean(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def fetch_market_cap_page_changes(market: str, page: int) -> list[float]:
    """Per-stock %-change for one page of Naver's KOSPI/KOSDAQ market-cap ranking."""
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
    changes = []
    for table_row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        if "code=" not in table_row:
            continue
        cells = [_clean(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", table_row, flags=re.I | re.S)]
        if len(cells) < 5:
            continue
        try:
            changes.append(float(cells[4].replace("%", "").replace(",", "")))
        except ValueError:
            continue
    return changes


def fetch_market_cap_page_moves(market: str, page: int) -> list[bool]:
    return [change > 0 for change in fetch_market_cap_page_changes(market, page)]


def _whole_market_snapshot_via_naver(pages_per_market: int) -> dict[str, float] | None:
    """Sampled from the top market-cap pages of both markets: full KOSPI+KOSDAQ
    has ~2600 tickers, and scraping every page on a 5-minute cron would be slow
    and rude to Naver."""
    changes: list[float] = []
    for market in ("KOSPI", "KOSDAQ"):
        for page in range(1, pages_per_market + 1):
            try:
                changes.extend(fetch_market_cap_page_changes(market, page))
            except Exception:
                continue
    if not changes:
        return None
    return {"up_ratio": sum(value > 0 for value in changes) / len(changes), "avg_change_pct": mean(changes)}


def whole_market_snapshot(pages_per_market: int = 6) -> dict[str, float] | None:
    """Whole-market breadth: advance/decline ratio and mean %-change.

    Prefers the official KRX Open API (every listed stock) when KRX_API_KEY is
    set; falls back to sampling Naver's market-cap ranking pages.
    """
    return _whole_market_snapshot_via_krx() or _whole_market_snapshot_via_naver(pages_per_market)


def whole_market_up_ratio(pages_per_market: int = 6) -> float | None:
    snapshot = whole_market_snapshot(pages_per_market)
    return snapshot["up_ratio"] if snapshot else None


def _cached_snapshot(max_cache_age_seconds: int = 600, pages_per_market: int = 6) -> dict[str, float] | None:
    if os.environ.get("NO_CACHE", "0") != "1" and os.path.exists(CACHE_PATH):
        if time.time() - os.path.getmtime(CACHE_PATH) <= max_cache_age_seconds:
            with open(CACHE_PATH, encoding="utf-8") as file:
                cached = json.load(file)
            if "avg_change_pct" in cached:
                return cached
    snapshot = whole_market_snapshot(pages_per_market)
    if snapshot is not None:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as file:
            json.dump(snapshot, file)
    return snapshot


def cached_whole_market_up_ratio(max_cache_age_seconds: int = 600, pages_per_market: int = 6) -> float | None:
    snapshot = _cached_snapshot(max_cache_age_seconds, pages_per_market)
    return snapshot["up_ratio"] if snapshot else None


def cached_whole_market_average_change_pct(max_cache_age_seconds: int = 600, pages_per_market: int = 6) -> float | None:
    snapshot = _cached_snapshot(max_cache_age_seconds, pages_per_market)
    return snapshot["avg_change_pct"] if snapshot else None
