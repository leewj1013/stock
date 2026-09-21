from __future__ import annotations

import json
import os
import urllib.request
import argparse
from datetime import datetime
from pathlib import Path


DEFAULT_CACHE = Path(".cache/sector_mapping.json")
# finance.naver.com/sise/sise_group.naver (the old 업종별시세 listing) was
# rebuilt as a client-rendered Next.js page -- the sector list and per-stock
# membership no longer appear anywhere in the server HTML, which is why the
# old regex scraper (parse_sector_list/parse_sector_detail against that URL)
# silently started returning nothing (2026-09-21 incident: every holding
# fell back to 미분류). Naver's mobile site still serves the same data as
# JSON: one industry code per ticker, and a name+membership page per code.
STOCK_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
INDUSTRY_URL = "https://m.stock.naver.com/api/stocks/industry/{code}"


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 stockAlarm sector-cache"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_sector_mapping(target_tickers: set[str] | None = None) -> tuple[dict[str, str], dict]:
    """One Naver industry name per ticker.

    A ticker's own industryCode is looked up first, then the code's Korean
    name is resolved from the industry page and cached per-code so tickers
    sharing an industry only look its name up once. A ticker Naver has no
    industry code for (delisted, newly listed, etc.) is just left unmapped
    rather than failing the whole batch.
    """
    mapping: dict[str, str] = {}
    industry_names: dict[str, str] = {}
    for ticker in sorted(target_tickers or ()):
        try:
            code = str(_fetch_json(STOCK_URL.format(code=ticker)).get("industryCode") or "")
        except (OSError, ValueError, TypeError):
            continue
        if not code:
            continue
        if code not in industry_names:
            try:
                industry_names[code] = str(_fetch_json(INDUSTRY_URL.format(code=code)).get("groupInfo", {}).get("name") or "")
            except (OSError, ValueError, TypeError):
                industry_names[code] = ""
        if industry_names[code]:
            mapping[ticker] = industry_names[code]
    return mapping, {"sector_count": len(industry_names)}


def save_sector_mapping(mapping: dict[str, str], metadata: dict, path: Path = DEFAULT_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": STOCK_URL,
               "mapping": dict(sorted(mapping.items())), **metadata}
    # Aggressive and neutral profiles both refresh this same cache file on
    # their own dashboard render; a plain write_text() truncates in place, so
    # two overlapping writers can interleave and leave a corrupt file behind
    # (this happened -- see 2026-09-21 incident, a leftover second JSON tail
    # after the closing brace). Writing to a per-process temp file and
    # rename()-ing it into place makes the swap atomic, so the file is always
    # either the old or the new complete payload, never a mix.
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def load_sector_mapping(tickers: set[str] | None = None, path: Path = DEFAULT_CACHE, refresh: bool = False,
                         retry_after_seconds: int = 600) -> dict[str, str]:
    if path.exists() and not refresh:
        payload = json.loads(path.read_text(encoding="utf-8"))
        mapping = {str(key): str(value) for key, value in payload.get("mapping", {}).items()}
        missing = (tickers or set()) - set(mapping)
        if not missing:
            return mapping
        # The requested watchlist has grown past what the cache covers (e.g. a
        # newly-held ticker never fetched before). Retry just the gap rather
        # than re-scraping everything. A ticker already known unmapped from a
        # prior attempt is throttled so a permanently-unmapped one (delisted,
        # no 업종 category) doesn't hit Naver every render; a ticker seen for
        # the first time is always worth one immediate try.
        previously_unmapped = set(payload.get("unmapped_tickers", []))
        try:
            fetched_at = datetime.fromisoformat(payload.get("fetched_at", ""))
            stale_enough = (datetime.now().astimezone() - fetched_at).total_seconds() >= retry_after_seconds
        except ValueError:
            stale_enough = True
        to_fetch = missing if stale_enough else missing - previously_unmapped
        if not to_fetch:
            return mapping
        fetched, metadata = fetch_sector_mapping(to_fetch)
        mapping.update(fetched)
        metadata["requested_tickers"] = len(tickers or ())
        metadata["mapped_tickers"] = len(mapping)
        metadata["unmapped_tickers"] = sorted((tickers or set()) - set(mapping))
        save_sector_mapping(mapping, metadata, path)
        return mapping
    mapping, metadata = fetch_sector_mapping(tickers)
    metadata["requested_tickers"] = len(tickers or ())
    metadata["mapped_tickers"] = len(mapping)
    metadata["unmapped_tickers"] = sorted((tickers or set()) - set(mapping))
    save_sector_mapping(mapping, metadata, path)
    return mapping


def main() -> None:
    from .app import configured_stocks, load_env
    load_env()
    parser = argparse.ArgumentParser(description="Cache Naver industry membership")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE); parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(); tickers = set(configured_stocks())
    mapping = load_sector_mapping(tickers, args.cache, args.refresh)
    print(json.dumps({"cache": str(args.cache), "requested": len(tickers), "mapped": len(tickers & set(mapping)), "unmapped": sorted(tickers - set(mapping))}, ensure_ascii=False))


if __name__ == "__main__": main()
