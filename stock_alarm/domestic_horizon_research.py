"""Prospective domestic swing and intraday observations; never place orders."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/domestic_horizon_research.db"
VERSION = "domestic-horizons-v1"
RULES = {
    "swing": {"distance_ma20_pct": [0, 5], "min_volume_ratio": 1,
              "min_relative_strength_pct": 0, "stop_atr_multiple": 2,
              "max_sessions": 15, "fee_bps_each_side": 15},
    "day": {"observation_time": ["09:30", "11:00"],
            "hypothesis": "first_30_minute_range_breakout_with_volume; flat_by_close",
            "required_data": "timestamped_intraday_ohlcv_and_executable_prices"},
}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(
            kind TEXT NOT NULL,ticker TEXT NOT NULL,signal_day TEXT NOT NULL,
            observed_at TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
            outcome TEXT,error TEXT,updated_at TEXT,
            PRIMARY KEY(kind,ticker,signal_day));
        CREATE TABLE IF NOT EXISTS minute_bars(
            ticker TEXT NOT NULL,bar_at TEXT NOT NULL,open REAL NOT NULL,
            high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,
            volume INTEGER NOT NULL,source TEXT NOT NULL,collected_at TEXT NOT NULL,
            PRIMARY KEY(ticker,bar_at));
        CREATE TABLE IF NOT EXISTS minute_quality(
            ticker TEXT NOT NULL,signal_day TEXT NOT NULL,checked_at TEXT NOT NULL,
            expected INTEGER NOT NULL,received INTEGER NOT NULL,missing INTEGER NOT NULL,
            opening_missing INTEGER NOT NULL,duplicate_source INTEGER NOT NULL,
            status TEXT NOT NULL,details TEXT NOT NULL,
            PRIMARY KEY(ticker,signal_day));
        CREATE TABLE IF NOT EXISTS day_signals(
            ticker TEXT NOT NULL,signal_day TEXT NOT NULL,rule_version TEXT NOT NULL,
            state TEXT NOT NULL,checked_at TEXT NOT NULL,signal_json TEXT,
            PRIMARY KEY(ticker,signal_day,rule_version));
    """)
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Domestic horizon research rules changed; use a new versioned database")
    db.commit()
    return db


def swing_eligible(row):
    try:
        values = [float(row[key]) for key in ("close", "atr20_pct", "distance_ma20_pct",
                                                "volume_ratio", "relative_strength_pct")]
    except (KeyError, TypeError, ValueError):
        return False
    close, atr, distance, volume, strength = values
    rule = RULES["swing"]
    return (all(math.isfinite(value) for value in values) and close > 0 and atr > 0
            and rule["distance_ma20_pct"][0] <= distance <= rule["distance_ma20_pct"][1]
            and volume >= rule["min_volume_ratio"] and strength > rule["min_relative_strength_pct"])


def harvest(source=DB_PATH, path=PATH):
    with closing(connect(path)) as db:
        start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        rows = query_rows("""SELECT c.*,r.market_date,r.strategy_version FROM candidate_snapshots c
            JOIN strategy_runs r ON r.run_id=c.run_id WHERE c.selected=1 AND c.evaluated_at>=?
            ORDER BY c.evaluated_at,c.run_id,c.ticker""", (start,), path=source)
        added = {"swing": 0, "day": 0}
        for row in rows:
            day, observed = row["evaluated_at"][:10], row["evaluated_at"]
            if row.get("market_date") != day:
                continue
            evidence = {key: row.get(key) for key in ("name", "close", "atr20_pct", "distance_ma20_pct",
                                                     "volume_ratio", "relative_strength_pct", "strategy_version")}
            if swing_eligible(row):
                added["swing"] += db.execute("""INSERT OR IGNORE INTO observations
                    (kind,ticker,signal_day,observed_at,payload,state) VALUES (?,?,?,?,?,'PENDING')""",
                    ("swing", row["ticker"], day, observed,
                     json.dumps({"version": VERSION, "evidence": evidence}, ensure_ascii=False))).rowcount
            clock = observed[11:16]
            if RULES["day"]["observation_time"][0] <= clock <= RULES["day"]["observation_time"][1]:
                added["day"] += db.execute("""INSERT OR IGNORE INTO observations
                    (kind,ticker,signal_day,observed_at,payload,state) VALUES (?,?,?,?,?,'DATA_REQUIRED')""",
                    ("day", row["ticker"], day, observed,
                     json.dumps({"version": VERSION, "evidence": evidence,
                                 "missing": RULES["day"]["required_data"]}, ensure_ascii=False))).rowcount
        db.commit()
    return added


def simulate_swing(payload, bars):
    """Next-open execution after the recommendation; close signals exit next open."""
    limit = RULES["swing"]["max_sessions"]
    if len(bars) < limit:
        return None
    bars = sorted(bars, key=lambda bar: str(bar[0]))
    if len({str(bar[0]) for bar in bars}) != len(bars):
        raise ValueError("Duplicate market dates")
    for bar in bars[:limit]:
        opening, high, low, close = map(float, bar[1:5])
        if (not all(math.isfinite(v) and v > 0 for v in (opening, high, low, close))
                or high < max(opening, close) or low > min(opening, close) or low > high):
            raise ValueError("Invalid OHLC price")
    entry = float(bars[0][1])
    atr_pct = float(payload["evidence"]["atr20_pct"])
    if not math.isfinite(atr_pct) or atr_pct <= 0 or entry <= 0:
        raise ValueError("Invalid entry evidence")
    stop_pct = min(10, max(5, atr_pct * RULES["swing"]["stop_atr_multiple"]))
    fixed_stop = entry * (1 - stop_pct / 100)
    peak_close, pending, exit_price, exit_day = entry, False, None, None
    for bar in bars[:limit]:
        opening, close = float(bar[1]), float(bar[4])
        if pending:
            exit_price, exit_day = opening, str(bar[0])
            break
        peak_close = max(peak_close, close)
        if close < fixed_stop or close < peak_close * (1 - stop_pct / 100):
            pending = True
    # Both paths use day-15 close as a marked liquidation value when still held.
    final_close = float(bars[limit - 1][4])
    fee = RULES["swing"]["fee_bps_each_side"] / 10000
    net = lambda price: (price / entry * (1 - fee) / (1 + fee) - 1) * 100
    swing_price = exit_price if exit_price is not None else final_close
    return {"entry_day": str(bars[0][0]), "entry_price": entry, "exit_day": exit_day,
            "exit_price": exit_price, "marked_at": str(bars[limit - 1][0]),
            "pending_exit": pending and exit_price is None,
            "swing_net_pct": net(swing_price), "hold_net_pct": net(final_close),
            "difference_pctp": net(swing_price) - net(final_close)}


def run(source=DB_PATH, path=PATH, today=None):
    from .app import naver_rows
    added = harvest(source, path)
    cutoff = (today or date.today()) - timedelta(days=1)
    updated = failures = 0
    with closing(connect(path)) as db:
        pending = db.execute("SELECT * FROM observations WHERE kind='swing' AND state='PENDING'").fetchall()
        for row in pending:
            signal = date.fromisoformat(row["signal_day"])
            if cutoff <= signal:
                continue
            try:
                history = naver_rows(row["ticker"], signal, cutoff)
                bars = [bar for bar in history if signal.strftime("%Y%m%d") < str(bar[0]) <= cutoff.strftime("%Y%m%d")]
                result = simulate_swing(json.loads(row["payload"]), bars)
                db.execute("""UPDATE observations SET outcome=?,state=?,error=NULL,updated_at=?
                    WHERE kind='swing' AND ticker=? AND signal_day=?""",
                    (json.dumps(result) if result else None, "COMPLETE" if result else "PENDING",
                     datetime.now().isoformat(timespec="seconds"), row["ticker"], row["signal_day"]))
                updated += 1
            except Exception as error:
                failures += 1
                db.execute("""UPDATE observations SET error=?,updated_at=?
                    WHERE kind='swing' AND ticker=? AND signal_day=?""",
                    (str(error), datetime.now().isoformat(timespec="seconds"), row["ticker"], row["signal_day"]))
        db.commit()
        counts = {row["kind"]: row["n"] for row in db.execute("SELECT kind,count(*) n FROM observations GROUP BY kind")}
    return {"version": VERSION, "added": added, "updated": updated, "failures": failures, "counts": counts}


def dashboard_section(path=PATH):
    rows = query_rows("SELECT * FROM observations ORDER BY observed_at DESC,kind,ticker LIMIT 20", path=path)
    counts = query_rows("SELECT kind,state,count(*) n FROM observations GROUP BY kind,state", path=path)
    summary = {(r["kind"], r["state"]): r["n"] for r in counts}
    minutes = query_rows("""SELECT o.ticker,o.signal_day,count(m.bar_at) AS bars
        FROM observations o LEFT JOIN minute_bars m ON m.ticker=o.ticker
          AND substr(m.bar_at,1,10)=o.signal_day AND substr(m.bar_at,12,5)>='09:00'
          AND substr(m.bar_at,12,5)<'15:30'
        WHERE o.kind='day' GROUP BY o.ticker,o.signal_day""", path=path)
    minute_counts = {(r["ticker"], r["signal_day"]): r["bars"] for r in minutes}
    quality_rows = query_rows("SELECT ticker,signal_day,expected,received,missing,opening_missing,status FROM minute_quality", path=path)
    quality = {(r["ticker"], r["signal_day"]): r for r in quality_rows}
    signal_rows = query_rows("SELECT ticker,signal_day,state FROM day_signals", path=path)
    signals = {(r["ticker"], r["signal_day"]): r["state"] for r in signal_rows}
    body = []
    for row in rows:
        outcome = json.loads(row["outcome"] or "null")
        key = row["ticker"], row["signal_day"]
        check = quality.get(key)
        signal_label = {"SIGNAL_RECORDED": "돌파 신호 기록", "NO_SIGNAL": "돌파 없음",
                        "QUALITY_BLOCKED": "품질 부족", "WAITING_OPENING": "개장 분봉 대기",
                        "OBSERVING": "관찰 중"}.get(signals.get(key), "관찰 대기")
        day_status = (f"{signal_label} · 분봉 {minute_counts.get(key, 0)}개"
                      + (f" · 누락 {check['missing']}개" if check and check["missing"] else "")
                      + (" · 수집 오류" if row["error"] else ""))
        cells = ["스윙" if row["kind"] == "swing" else "단타", row["signal_day"],
                 json.loads(row["payload"])["evidence"].get("name") or row["ticker"],
                 f"{outcome['swing_net_pct']:+.2f}%" if outcome else "—",
                 f"{outcome['hold_net_pct']:+.2f}%" if outcome else "—",
                 day_status
                 if row["kind"] == "day" else "수집 오류" if row["error"] else "비교 완료" if outcome else "관찰 중"]
        body.append("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells) + "</tr>")
    table = ("<table><thead><tr><th>방식</th><th>추천일</th><th>종목</th><th>실험 순수익률</th>"
             "<th>동일 종목 15일 보유</th><th>상태</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"
             if body else "<p class='muted'>등록 이후의 추천을 기다리고 있습니다.</p>")
    return ("<section><h2>국내 스윙·단타 관찰</h2>"
            f"<p class='muted'>스윙 {summary.get(('swing', 'COMPLETE'), 0)}건 비교 완료 · "
            f"단타 {summary.get(('day', 'DATA_REQUIRED'), 0)}건 관찰 · 정규장 분봉 수집 중</p>"
            "<p class='muted'>스윙은 추천 다음 거래일 시가 진입 후 추세·손절 이탈 시 다음 시가 청산, "
            "최대 15거래일 관찰입니다. 양쪽에 왕복 비용을 반영한 단일 종목 비교이며 계좌 수익률이 아닙니다. "
            "단타는 후보 관찰 이후 10:59까지의 첫 30분 가격대 돌파와 거래량을 검사합니다. 신호가 기록되어도 체결 가능 가격·누락 구간 검증 전에는 수익률을 계산하지 않습니다.</p>"
            + table + "</section>")
