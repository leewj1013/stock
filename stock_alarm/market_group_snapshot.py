"""Point-in-time Naver industry and theme rankings for dashboard observation."""
from __future__ import annotations

import json
import math
import sqlite3
import urllib.request
from contextlib import closing
from datetime import date, datetime
from html import escape
from pathlib import Path

PATH = "data/market_group_snapshot.db"
BASE = "https://stock.naver.com/api/stockSecurity/rankings/v2/domestic"
CATEGORIES = ("industries", "themes")


def fetch(category: str) -> list[dict]:
    if category not in CATEGORIES:
        raise ValueError("Unknown market group category")
    suffix = "&excludeCodes=25" if category == "themes" else ""
    request = urllib.request.Request(
        f"{BASE}/{category}?sortType=changeRate&size=100&period=daily{suffix}",
        headers={"User-Agent": "Mozilla/5.0 stockAlarm market-observation"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError(f"Empty or invalid {category} ranking")
    return items


def collect(path: str = PATH, snapshots: dict[str, list[dict]] | None = None) -> dict[str, int]:
    # Fetch both before writing: a failed response must not leave a half-updated pair.
    snapshots = snapshots if snapshots is not None else {category: fetch(category) for category in CATEGORIES}
    rows = []
    for category in CATEGORIES:
        items = snapshots[category]
        if not items:
            raise ValueError(f"Empty {category} ranking")
        for item in items:
            updated = str(item.get("updatedAt") or "")
            try:
                market_day = date.fromisoformat(updated[:10])
                datetime.fromisoformat(updated)
                change = float(item["changeRate"])
                market_cap = int(item.get("totalMarketCap") or 0)
                trading_value = int(item.get("totalTradingValue") or 0)
                rising = int(item.get("risingCount") or 0)
                falling = int(item.get("fallingCount") or 0)
                unchanged = int(item.get("unchangedCount") or 0)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Invalid {category} ranking item") from error
            if market_day > date.today() or not item.get("code") or not item.get("name") or not math.isfinite(change) or min(market_cap, trading_value, rising, falling, unchanged) < 0:
                raise ValueError(f"Invalid {category} ranking item")
            rows.append((category, market_day.isoformat(), str(item["code"]), str(item["name"]), updated,
                         change, market_cap, trading_value, rising, falling, unchanged,
                         json.dumps(item, ensure_ascii=False)))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            category TEXT NOT NULL,market_day TEXT NOT NULL,code TEXT NOT NULL,name TEXT NOT NULL,
            source_time TEXT NOT NULL,change_pct REAL NOT NULL,market_cap INTEGER NOT NULL,
            trading_value INTEGER NOT NULL,rising INTEGER NOT NULL,falling INTEGER NOT NULL,
            unchanged INTEGER NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(category,market_day,code))""")
        db.executemany("""INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(category,market_day,code) DO UPDATE SET
            name=excluded.name,source_time=excluded.source_time,change_pct=excluded.change_pct,
            market_cap=excluded.market_cap,trading_value=excluded.trading_value,
            rising=excluded.rising,falling=excluded.falling,unchanged=excluded.unchanged,payload=excluded.payload
            WHERE excluded.source_time >= snapshots.source_time""", rows)
        db.commit()
    return {category: sum(row[0] == category for row in rows) for category in CATEGORIES}


def latest(category: str, path: str = PATH) -> list[dict]:
    if category not in CATEGORIES or not Path(path).exists():
        return []
    with closing(sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        day = db.execute("SELECT MAX(market_day) FROM snapshots WHERE category=?", (category,)).fetchone()[0]
        if not day:
            return []
        return [dict(row) for row in db.execute(
            "SELECT * FROM snapshots WHERE category=? AND market_day=? ORDER BY change_pct DESC,code",
            (category, day))]


def dashboard_section(path: str = PATH) -> str:
    industries, themes = latest("industries", path), latest("themes", path)
    if not industries and not themes:
        return "<section><h2>업종·테마 동향</h2><p class='muted'>수집된 데이터가 없습니다.</p></section>"

    def ranking(title: str, rows: list[dict], by_cap: bool = False) -> str:
        ordered = sorted(rows, key=lambda row: (-row["market_cap"], row["code"])) if by_cap else rows
        selected = ordered[:10 if by_cap else 5]
        total_cap = sum(row["market_cap"] for row in selected) if by_cap else 0
        cells = []
        for index, row in enumerate(selected, 1):
            payload = json.loads(row["payload"])
            leaders = payload.get("topByMarketCap" if by_cap else "topByTradingValue") or []
            leader = ", ".join(str(item.get("name") or "") for item in leaders[:3]) or "-"
            breadth_total = row["rising"] + row["falling"] + row["unchanged"]
            breadth = f"{row['rising'] / breadth_total:.0%}" if breadth_total else "-"
            amount = f"{row['market_cap'] / 1e12:,.1f}조원" if by_cap else f"{row['trading_value'] / 1e8:,.0f}억원"
            weight = f"<td>{row['market_cap'] / total_cap:.1%}</td>" if by_cap and total_cap else ""
            cells.append(f"<tr><td>{index}</td><td>{escape(row['name'])}</td><td>{row['change_pct']:+.2f}%</td>"
                         f"<td>{amount}</td><td>{breadth}</td>{weight}<td>{escape(leader)}</td></tr>")
        extra = "<th>상위 10개 내 비중</th>" if by_cap else ""
        amount_head = "시가총액" if by_cap else "거래대금"
        return (f"<h3>{title}</h3><div class='table-scroll'><table><thead><tr><th>순위</th><th>이름</th><th>등락률</th>"
                f"<th>{amount_head}</th><th>상승 비율</th>{extra}<th>대표 종목</th></tr></thead><tbody>"
                + "".join(cells) + "</tbody></table></div>") if selected else f"<h3>{title}</h3><p class='muted'>수집 전입니다.</p>"

    times = [row["source_time"] for row in industries + themes]
    source_time = min(times) if times else "-"
    return ("<section><h2>업종·테마 동향</h2>"
            f"<p class='muted'>네이버 증시 · 데이터 기준 {escape(source_time)} · 관찰용</p>"
            + ranking("업종 등락률 TOP 5", industries)
            + ranking("테마 등락률 TOP 5", themes)
            + ranking("업종 시가총액 TOP 10", industries, by_cap=True)
            + "</section>")


if __name__ == "__main__":
    print(collect())
