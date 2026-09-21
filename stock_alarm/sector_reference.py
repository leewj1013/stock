from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
import argparse
from datetime import datetime
from pathlib import Path


DEFAULT_CACHE = Path(".cache/sector_mapping.json")
LIST_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver"


def _decode(body: bytes) -> str:
    for encoding in ("euc-kr", "cp949", "utf-8"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", value))).strip()


def parse_sector_list(content: str) -> list[dict[str, str]]:
    """Parse Naver upjong links regardless of query-parameter order."""
    output, seen = [], set()
    for href, label in re.findall(r"<a[^>]+href=[\"']([^\"']*sise_group_detail\.naver\?[^\"']+)[\"'][^>]*>(.*?)</a>", content, re.I | re.S):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(href)).query)
        number = (query.get("no") or [""])[0]
        if not number or number in seen:
            continue
        name = _clean(label)
        if name:
            seen.add(number); output.append({"number": number, "sector": name})
    return output


def parse_sector_detail(content: str) -> list[dict[str, str]]:
    output, seen = [], set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.I | re.S):
        match = re.search(r"(?:/item/main\.naver\?code=|[?&]code=)(\d{6})", row, re.I)
        if not match or match.group(1) in seen:
            continue
        name_match = re.search(r"class=[\"']tltle[\"'][^>]*>(.*?)</a>", row, re.I | re.S)
        seen.add(match.group(1)); output.append({"ticker": match.group(1), "name": _clean(name_match.group(1)) if name_match else ""})
    return output


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 stockAlarm sector-cache"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return _decode(response.read())


def fetch_sector_mapping(target_tickers: set[str] | None = None) -> tuple[dict[str, str], dict]:
    sectors = parse_sector_list(_fetch(LIST_URL))
    mapping, duplicates = {}, {}
    for item in sectors:
        url = DETAIL_URL + "?" + urllib.parse.urlencode({"type": "upjong", "no": item["number"]})
        for stock in parse_sector_detail(_fetch(url)):
            ticker = stock["ticker"]
            if target_tickers is not None and ticker not in target_tickers:
                continue
            if ticker in mapping and mapping[ticker] != item["sector"]:
                duplicates.setdefault(ticker, [mapping[ticker]]).append(item["sector"])
                continue
            mapping[ticker] = item["sector"]
    return mapping, {"sector_count": len(sectors), "duplicate_memberships": duplicates}


def save_sector_mapping(mapping: dict[str, str], metadata: dict, path: Path = DEFAULT_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"), "source": LIST_URL,
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
