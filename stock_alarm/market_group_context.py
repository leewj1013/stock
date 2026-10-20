"""Attach observed market-group membership to recommendations, without changing scores."""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from contextlib import closing
from datetime import date, datetime
from html import escape
from pathlib import Path
from statistics import mean
from urllib.parse import quote

from .data_store import DB_PATH, query_rows
from .market_group_snapshot import PATH, latest


def _json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 stockAlarm market-observation"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def theme_members(code: str) -> set[str]:
    if not code.isdigit():
        raise ValueError("Invalid theme code")
    members = set()
    cursor = ""
    seen_cursors = set()
    for _ in range(100):
        suffix = f"&cursor={quote(cursor, safe='')}" if cursor else ""
        data = _json(f"https://stock.naver.com/api/domestic/market/theme/{code}/stocklist?pageSize=100&sortType=changeRate{suffix}")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError(f"Invalid theme stock list: {code}")
        members.update(str(item["itemCode"]) for item in items)
        if not data.get("hasNext"):
            return members
        cursor = str(data.get("cursor") or "")
        if not cursor or cursor in seen_cursors:
            raise ValueError(f"Invalid theme page cursor: {code}")
        seen_cursors.add(cursor)
    raise ValueError(f"Theme stock list exceeds 100 pages: {code}")


def _before_or_equal(source_time: str, observed_at: str) -> bool:
    source, observed = datetime.fromisoformat(source_time), datetime.fromisoformat(observed_at)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=source.tzinfo)
    return source <= observed


def connect(path: str = PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS recommendation_context (
        pick_day TEXT NOT NULL,ticker TEXT NOT NULL,name TEXT NOT NULL,observed_at TEXT NOT NULL,
        source_time TEXT NOT NULL,industry_code TEXT,industry_name TEXT,industry_rank INTEGER,
        top_theme_codes TEXT NOT NULL,top_theme_names TEXT NOT NULL,top_theme_rank INTEGER,
        industry_breadth REAL,theme_breadth REAL,payload TEXT NOT NULL,
        PRIMARY KEY(pick_day,ticker))""")
    db.commit()
    return db


def harvest(source: str = DB_PATH, path: str = PATH, today: date | None = None) -> int:
    day = (today or date.today()).isoformat()
    selected = query_rows("""SELECT c.ticker,c.name,c.evaluated_at FROM candidate_snapshots c
        JOIN strategy_runs r ON r.run_id=c.run_id
        WHERE r.market_date=? AND c.selected=1 ORDER BY c.evaluated_at DESC""", (day,), path=source)
    if not selected:
        return 0
    industries, themes = latest("industries", path), latest("themes", path)
    if not industries or not themes or industries[0]["market_day"] != day or themes[0]["market_day"] != day:
        return 0
    selected = list({row["ticker"]: row for row in reversed(selected)}.values())
    with closing(connect(path)) as db:
        existing = {row[0] for row in db.execute("SELECT ticker FROM recommendation_context WHERE pick_day=?", (day,))}
        selected = [row for row in selected if row["ticker"] not in existing]
        if not selected:
            return 0
        source_time = max(row["source_time"] for row in industries + themes)
        selected = [row for row in selected if _before_or_equal(source_time, row["evaluated_at"])]
        if not selected:
            return 0
        industry_by_code = {row["code"]: (rank, row) for rank, row in enumerate(industries, 1)}
        cap_rank = {row["code"]: rank for rank, row in enumerate(
            sorted(industries, key=lambda row: (-row["market_cap"], row["code"])), 1)}
        theme_top = themes[:5]
        memberships = {row["code"]: theme_members(row["code"]) for row in theme_top}
        count = 0
        for candidate in selected:
            ticker = candidate["ticker"]
            if not (ticker.isdigit() and len(ticker) == 6):
                continue
            info = _json(f"https://m.stock.naver.com/api/stock/{ticker}/integration")
            industry_code = str(info.get("industryCode") or "")
            industry_rank, industry = industry_by_code.get(industry_code, (None, None))
            matched = [(rank, row) for rank, row in enumerate(theme_top, 1) if ticker in memberships[row["code"]]]
            top_theme_rank = min((rank for rank, _ in matched), default=None)
            primary_theme = next((row for rank, row in matched if rank == top_theme_rank), None)
            def breadth(row):
                if not row:
                    return None
                total = row["rising"] + row["falling"] + row["unchanged"]
                return row["rising"] / total if total else None
            theme_payload = json.loads(primary_theme["payload"]) if primary_theme else {}
            industry_payload = json.loads(industry["payload"]) if industry else {}
            context = {"industry_cap_rank": cap_rank.get(industry_code),
                       "is_theme_trading_leader": bool(theme_payload.get("topByTradingValue") and
                           str(theme_payload["topByTradingValue"][0].get("code")) == ticker),
                       "is_large_industry_cap_leader": bool(cap_rank.get(industry_code, 999) <= 10 and
                           industry_payload.get("topByMarketCap") and
                           str(industry_payload["topByMarketCap"][0].get("code")) == ticker),
                       "industry_change_pct": industry["change_pct"] if industry else None,
                       "industry_trading_value": industry["trading_value"] if industry else None,
                       "top_theme_change_pct": primary_theme["change_pct"] if primary_theme else None,
                       "top_theme_trading_value": primary_theme["trading_value"] if primary_theme else None}
            count += db.execute("""INSERT OR IGNORE INTO recommendation_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (day, ticker, candidate["name"] or ticker, candidate["evaluated_at"], source_time,
                 industry_code or None, industry["name"] if industry else None, industry_rank,
                 json.dumps([row["code"] for _, row in matched]),
                 json.dumps([row["name"] for _, row in matched], ensure_ascii=False), top_theme_rank,
                 breadth(industry), breadth(primary_theme), json.dumps(context, ensure_ascii=False))).rowcount
        db.commit()
    return count


def dashboard_section(path: str = PATH, outcome_path: str = DB_PATH) -> str:
    if not Path(path).exists():
        return "<section><h2>추천 시점 업종·테마 관찰</h2><p class='muted'>관찰 기록이 없습니다.</p></section>"
    rows = query_rows("SELECT * FROM recommendation_context ORDER BY observed_at DESC,ticker", path=path)
    if not rows:
        return "<section><h2>추천 시점 업종·테마 관찰</h2><p class='muted'>관찰 기록이 없습니다.</p></section>"
    outcomes = query_rows("""SELECT pick_date,ticker,return_1d_pct,return_3d_pct,return_5d_pct,
        return_10d_pct,return_20d_pct FROM recommendation_outcomes WHERE quality_status='valid'
        ORDER BY updated_at DESC""", path=outcome_path)
    by_key = {(row["pick_date"], row["ticker"]): row for row in reversed(outcomes)}
    completed = [(row, by_key[(row["pick_day"], row["ticker"])]["return_10d_pct"])
                 for row in rows if (row["pick_day"], row["ticker"]) in by_key
                 and by_key[(row["pick_day"], row["ticker"])].get("return_10d_pct") is not None]
    comparisons = []
    conditions = [
        ("상승률 상위 5개 업종", lambda row: row["industry_rank"] is not None and row["industry_rank"] <= 5),
        ("상승률 상위 5개 테마", lambda row: row["top_theme_rank"] is not None),
        ("업종 상승 종목 50% 이상", lambda row: row["industry_breadth"] is not None and row["industry_breadth"] >= .5),
        ("테마 상승 종목 50% 이상", lambda row: row["theme_breadth"] is not None and row["theme_breadth"] >= .5),
        ("테마 거래대금 대장주", lambda row: json.loads(row["payload"]).get("is_theme_trading_leader", False)),
        ("시총 TOP 10 업종 대표주", lambda row: json.loads(row["payload"]).get("is_large_industry_cap_leader", False)),
    ]
    for label, condition in conditions:
        matched = [float(value) for row, value in completed if condition(row)]
        others = [float(value) for row, value in completed if not condition(row)]
        if matched and others:
            values = [label, len(matched), f"{mean(matched):+.2f}%", len(others), f"{mean(others):+.2f}%",
                      f"{mean(matched) - mean(others):+.2f}%p"]
            comparisons.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>")
    body = []
    for row in rows[:30]:
        outcome = by_key.get((row["pick_day"], row["ticker"]), {})
        values = [row["pick_day"], row["name"], row["industry_name"] or "미분류",
                  row["industry_rank"] or "-", ", ".join(json.loads(row["top_theme_names"])) or "해당 없음",
                  row["top_theme_rank"] or "-", f"{row['industry_breadth']:.0%}" if row["industry_breadth"] is not None else "-",
                  f"{row['theme_breadth']:.0%}" if row["theme_breadth"] is not None else "-"]
        values += [f"{outcome[f'return_{days}d_pct']:+.2f}%" if outcome.get(f"return_{days}d_pct") is not None else "관찰 중"
                   for days in (1, 3, 5, 10, 20)]
        body.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>")
    heads = ["추천일", "종목", "업종", "업종 순위", "TOP 5 테마", "테마 순위", "업종 상승 비율", "테마 상승 비율",
             "1일", "3일", "5일", "10일", "20일"]
    return ("<section><h2>추천 시점 업종·테마 관찰</h2>"
            "<p class='muted'>추천 전에 저장한 순위와 소속만 기록합니다. 수익률은 기존 추천 성과 DB의 다음 거래일 진입 기준입니다.</p>"
            + ("<h3>10거래일 성과 비교</h3><p class='muted'>표본이 양쪽에 있는 조건만 표시합니다. 중복 종목과 시장 국면 차이를 통제하지 않은 단순 관찰입니다.</p>"
               "<div class='table-scroll'><table><thead><tr><th>조건</th><th>해당 종목</th><th>평균 수익률</th><th>그 외</th><th>평균 수익률</th><th>차이</th></tr></thead><tbody>"
               + "".join(comparisons) + "</tbody></table></div>" if comparisons else
               "<p class='muted'>10거래일 완료 표본이 양쪽에 쌓이면 조건별 성과 비교를 표시합니다.</p>")
            + "<div class='table-scroll'><table><thead><tr>" + "".join(f"<th>{head}</th>" for head in heads)
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div></section>")


if __name__ == "__main__":
    print({"recommendation_context": harvest()})
