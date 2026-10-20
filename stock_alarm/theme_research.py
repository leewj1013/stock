"""Prospective theme experiments from daily close snapshots; no live orders."""
from __future__ import annotations

import json
import math
import sqlite3
import urllib.request
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .data_store import query_rows

PATH = "data/theme_research.db"
VERSION = "theme-observation-v1"
URL = "https://stock.naver.com/api/stockSecurity/rankings/v2/domestic/themes?sortType=changeRate&size=100&excludeCodes=25&period=daily"
RULES = {"minimum_members": 3, "minimum_breadth": 0.5, "minimum_change_pct": 2,
         "minimum_trading_value": 5_000_000_000, "weak_breadth": 0.35,
         "weak_sessions": 2, "early_reduction": 0.5, "fee_bps_each_side": 15,
         "horizons": [1, 3, 5, 10, 20]}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(
            market_day TEXT NOT NULL,theme_code TEXT NOT NULL,theme_name TEXT NOT NULL,
            change_pct REAL NOT NULL,rising INTEGER NOT NULL,falling INTEGER NOT NULL,
            unchanged INTEGER NOT NULL,total INTEGER NOT NULL,breadth REAL NOT NULL,
            trading_value INTEGER NOT NULL,leader_ticker TEXT,leader_name TEXT,
            source_time TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(market_day,theme_code));
        CREATE TABLE IF NOT EXISTS episodes(
            theme_code TEXT NOT NULL,signal_day TEXT NOT NULL,theme_name TEXT NOT NULL,
            leader_ticker TEXT NOT NULL,leader_name TEXT NOT NULL,state TEXT NOT NULL,
            outcomes TEXT,error TEXT,updated_at TEXT NOT NULL,
            PRIMARY KEY(theme_code,signal_day));
    """)
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True)), ("source", URL)):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Theme research rules changed; create a new versioned database")
    db.commit()
    return db


def fetch_snapshot(url=URL) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 stockAlarm theme-observation"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("items") or []


def normalize(item: dict) -> dict | None:
    leaders = item.get("topByTradingValue") or []
    updated = str(item.get("updatedAt") or "")
    rising, falling, unchanged = (int(item.get(key) or 0) for key in ("risingCount", "fallingCount", "unchangedCount"))
    total = rising + falling + unchanged
    if not item.get("code") or not updated[:10] or total <= 0 or not leaders:
        return None
    return {"market_day": updated[:10], "theme_code": str(item["code"]), "theme_name": str(item.get("name") or item["code"]),
            "change_pct": float(item.get("changeRate") or 0), "rising": rising, "falling": falling,
            "unchanged": unchanged, "total": total, "breadth": rising / total,
            "trading_value": int(item.get("totalTradingValue") or 0),
            "leader_ticker": str(leaders[0].get("code") or ""), "leader_name": str(leaders[0].get("name") or ""),
            "source_time": updated, "payload": item}


def eligible(row: dict) -> bool:
    return (int(row["total"]) >= RULES["minimum_members"] and float(row["breadth"]) >= RULES["minimum_breadth"]
            and float(row["change_pct"]) >= RULES["minimum_change_pct"]
            and int(row["trading_value"]) >= RULES["minimum_trading_value"] and bool(row["leader_ticker"]))


def collect(path=PATH, items=None) -> dict:
    items = fetch_snapshot() if items is None else items
    stored = episodes = 0
    with closing(connect(path)) as db:
        for item in items:
            row = normalize(item)
            if row is None or date.fromisoformat(row["market_day"]) > date.today():
                continue
            previous = db.execute("SELECT * FROM snapshots WHERE theme_code=? AND market_day<? ORDER BY market_day DESC LIMIT 1",
                                  (row["theme_code"], row["market_day"])).fetchone()
            stored += db.execute("""INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["market_day"], row["theme_code"], row["theme_name"], row["change_pct"], row["rising"], row["falling"],
                 row["unchanged"], row["total"], row["breadth"], row["trading_value"], row["leader_ticker"], row["leader_name"],
                 row["source_time"], json.dumps(row["payload"], ensure_ascii=False))).rowcount
            if eligible(row) and not (previous and eligible(dict(previous))):
                episodes += db.execute("INSERT OR IGNORE INTO episodes VALUES (?,?,?,?,?,'PENDING',NULL,NULL,?)",
                    (row["theme_code"], row["market_day"], row["theme_name"], row["leader_ticker"], row["leader_name"],
                     datetime.now().isoformat(timespec="seconds"))).rowcount
        db.commit()
    return {"snapshots": stored, "episodes": episodes}


def _price_rows(rows):
    ordered = sorted(rows, key=lambda row: str(row[0]))
    if len({str(row[0]) for row in ordered}) != len(ordered):
        raise ValueError("Duplicate market dates")
    for row in ordered:
        values = [float(value) for value in row[1:5]]
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Invalid OHLC price")
        opening, high, low, close = values
        if high < max(opening, close) or low > min(opening, close) or low > high:
            raise ValueError("Inconsistent OHLC price")
    return ordered


def weak_trigger_day(snapshots: list[dict]) -> str | None:
    consecutive = 0
    for row in snapshots:
        weak = float(row["breadth"]) < RULES["weak_breadth"] or float(row["change_pct"]) < 0
        consecutive = consecutive + 1 if weak else 0
        if consecutive >= RULES["weak_sessions"]:
            return str(row["market_day"])
    return None


def _return(entry: float | None, close: float, fee: float) -> float | None:
    return None if not entry else (close / entry * (1 - fee) / (1 + fee) - 1) * 100


def _current_exit_path(ticker: str, signal_day: str, bars: list[list], first_index: int,
                       weak_day: str | None = None) -> dict[str, float]:
    from .sell_check import _evaluate_position
    fee = RULES["fee_bps_each_side"] / 10000
    entry = float(bars[first_index][1])
    held, cash, previous, peak = 1.0, 0.0, None, None
    pending: tuple[float, bool] | None = None
    partial_taken = theme_reduced = False
    outcomes = {}
    entry_key = str(bars[first_index][0])
    position = {"ticker": ticker, "name": ticker, "entry_price": str(entry),
                "entry_date": f"{entry_key[:4]}-{entry_key[4:6]}-{entry_key[6:]}T09:00:00"}
    for index in range(first_index, len(bars)):
        opening, close = float(bars[index][1]), float(bars[index][4])
        if pending and held:
            fraction, is_take_profit = pending
            sold = min(held, fraction)
            cash += sold * opening / entry * (1 - fee)
            held -= sold
            partial_taken = partial_taken or is_take_profit
            pending = None
        day_key = str(bars[index][0])
        current_return = (close / entry - 1) * 100
        if held:
            evaluation = _evaluate_position(position, date.fromisoformat(f"{day_key[:4]}-{day_key[4:6]}-{day_key[6:]}"),
                                            previous, peak, price_rows=bars[:index + 1], partial_taken=partial_taken,
                                            remaining_quantity=max(1, round(held * 100)), sell_policy=None)
            if evaluation and evaluation["reasons"]:
                partial = evaluation["sale_type"] == "partial"
                fraction = held * evaluation["quantity_fraction"] if partial else held
                pending = (fraction, partial)
            if weak_day and not theme_reduced and day_key >= weak_day.replace("-", ""):
                # A full current-rule exit remains stronger; otherwise reduce half once.
                if not pending or pending[0] < held:
                    pending = (held * RULES["early_reduction"], False)
                theme_reduced = True
        previous = current_return
        peak = current_return if peak is None else max(peak, current_return)
        horizon = index - first_index + 1
        if horizon in RULES["horizons"]:
            liquidation = cash + held * close / entry * (1 - fee)
            outcomes[str(horizon)] = (liquidation / (1 + fee) - 1) * 100
    return outcomes


def simulate(signal_day: str, bars: list[list], snapshots: list[dict]) -> dict:
    bars = _price_rows(bars)
    after = [index for index, row in enumerate(bars) if str(row[0]) > signal_day.replace("-", "")]
    if not after:
        return {}
    first_index, fee = after[0], RULES["fee_bps_each_side"] / 10000
    first_entry = float(bars[first_index][1])
    future_snapshots = sorted((row for row in snapshots if str(row["market_day"]) > signal_day), key=lambda row: str(row["market_day"]))
    confirm_day = str(future_snapshots[0]["market_day"]) if future_snapshots and eligible(future_snapshots[0]) else None
    confirmed_index = next((i for i, row in enumerate(bars) if confirm_day and str(row[0]) > confirm_day.replace("-", "")), None)
    confirmed_entry = float(bars[confirmed_index][1]) if confirmed_index is not None else None
    weak_day = weak_trigger_day(future_snapshots)
    current_path = _current_exit_path("THEME", signal_day, bars, first_index)
    weakness_path = _current_exit_path("THEME", signal_day, bars, first_index, weak_day)
    horizons = {}
    for horizon in RULES["horizons"]:
        index = first_index + horizon - 1
        if index >= len(bars):
            continue
        close, as_of = float(bars[index][4]), str(bars[index][0])
        group_factor = 1.0
        group_days = 0
        for snapshot in future_snapshots:
            key = str(snapshot["market_day"]).replace("-", "")
            if key <= as_of:
                group_factor *= 1 + float(snapshot["change_pct"]) / 100
                group_days += 1
        leader = _return(first_entry, close, fee)
        related = (group_factor - 1) * 100 if group_days >= horizon else None
        confirmed = _return(confirmed_entry, close, fee) if confirmed_index is not None and confirmed_index <= index else None
        current, early = current_path.get(str(horizon)), weakness_path.get(str(horizon))
        horizons[str(horizon)] = {"as_of": as_of, "leader_return_pct": leader, "related_return_pct": related,
            "leader_minus_related_pctp": None if related is None else leader - related,
            "first_entry_return_pct": leader, "confirmed_entry_return_pct": confirmed,
            "confirmed_minus_first_pctp": None if confirmed is None else confirmed - leader,
            "current_rule_return_pct": current, "weakness_reduction_return_pct": early,
            "weakness_minus_current_pctp": None if early is None or current is None else early - current}
    return {"first_entry_day": str(bars[first_index][0]), "confirmed_signal_day": confirm_day,
            "confirmed_entry_day": str(bars[confirmed_index][0]) if confirmed_index is not None else None,
            "weak_trigger_day": weak_day, "horizons": horizons}


def run(path=PATH, today=None, items=None):
    from .app import naver_rows
    collected = collect(path, items)
    cutoff = (today or date.today()) - timedelta(days=1)
    updated = failures = 0
    with closing(connect(path)) as db:
        for episode in db.execute("SELECT * FROM episodes WHERE state='PENDING'").fetchall():
            signal = date.fromisoformat(episode["signal_day"])
            if cutoff <= signal:
                continue
            try:
                bars = naver_rows(episode["leader_ticker"], signal - timedelta(days=60), cutoff)
                snapshots = [dict(row) for row in db.execute("SELECT * FROM snapshots WHERE theme_code=? AND market_day>=? ORDER BY market_day",
                                                             (episode["theme_code"], episode["signal_day"]))]
                result = simulate(episode["signal_day"], bars, snapshots)
                state = "COMPLETE" if "20" in result.get("horizons", {}) else "PENDING"
                db.execute("UPDATE episodes SET outcomes=?,state=?,error=NULL,updated_at=? WHERE theme_code=? AND signal_day=?",
                           (json.dumps(result), state, datetime.now().isoformat(timespec="seconds"), episode["theme_code"], episode["signal_day"]))
                updated += 1
            except Exception as exc:
                failures += 1
                db.execute("UPDATE episodes SET error=?,updated_at=? WHERE theme_code=? AND signal_day=?",
                           (str(exc), datetime.now().isoformat(timespec="seconds"), episode["theme_code"], episode["signal_day"]))
            db.commit()
        total = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    return {"version": VERSION, **collected, "updated": updated, "failures": failures, "total_episodes": total}


def dashboard_section(path=PATH):
    if not Path(path).exists():
        return "<section><h2>테마주 관찰</h2><p class='muted'>관찰 DB 생성 전입니다.</p></section>"
    rows = query_rows("SELECT * FROM episodes ORDER BY signal_day DESC,theme_code LIMIT 20", path=path)
    counts = query_rows("SELECT COUNT(*) total,SUM(state='COMPLETE') complete,SUM(error IS NOT NULL) errors FROM episodes", path=path)[0]
    body = []
    for row in rows:
        outcome = json.loads(row["outcomes"] or "{}")
        ten = outcome.get("horizons", {}).get("10", {})
        values = [row["signal_day"], row["theme_name"], row["leader_name"],
                  f"{ten['leader_minus_related_pctp']:+.2f}%p" if ten.get("leader_minus_related_pctp") is not None else "관찰 중",
                  f"{ten['confirmed_minus_first_pctp']:+.2f}%p" if ten.get("confirmed_minus_first_pctp") is not None else "관찰 중",
                  f"{ten['weakness_minus_current_pctp']:+.2f}%p" if ten.get("weakness_minus_current_pctp") is not None else "관찰 중",
                  "오류" if row["error"] else "완료" if row["state"] == "COMPLETE" else "관찰 중"]
        body.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>")
    heads = ["포착일", "테마", "거래대금 대장주", "대장주-테마전체", "하루확인-최초진입", "약화축소-기준", "상태"]
    table = ("<table><thead><tr>" + "".join(f"<th>{head}</th>" for head in heads) + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
             if rows else "<p class='muted'>조건을 충족한 새 테마를 기다리고 있습니다.</p>")
    return ("<section><div class='table-heading'><h2>테마주 관찰</h2>"
            f"<span class='table-count'>전체 {counts['total']}건 · 완료 {counts['complete'] or 0}건 · 오류 {counts['errors'] or 0}건</span></div>"
            "<p class='muted'>관찰 전용 · 테마 3종목 이상 · 상승 확산 50% 이상 · 테마 +2% 이상 · 거래대금 50억원 이상. 10거래일 차이는 양수일수록 비교안이 유리합니다.</p>"
            "<p class='muted'>대장주와 테마 전체, 최초 포착과 하루 확인, 현재 손절·추세·분할익절 규칙과 테마 약화 시 절반 축소를 비교합니다. 네이버 테마 분류 변경 가능성이 있어 실제 주문에는 사용하지 않습니다.</p>" + table + "</section>")


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
