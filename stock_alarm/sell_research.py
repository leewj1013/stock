"""Versioned, observation-only exit experiments. Never creates orders or alerts."""
from __future__ import annotations

import json
import sqlite3
from html import escape
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/sell_research.db"
VERSION = "exit-observation-v1"
RULES = {"risk_floor_pct": 5.0, "risk_cap_pct": 10.0, "atr_multiple": 2.0,
         "defer_sessions": 1, "partial_fraction": 0.5, "trail_floor_pct": 4.0,
         "sell_cost_bps": 30, "horizons": [1, 3, 5, 10]}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS risks (
            position_id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, limit_pct REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (
            run_id TEXT NOT NULL, position_id TEXT NOT NULL, checked_at TEXT NOT NULL,
            decision TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id, position_id));
        CREATE TABLE IF NOT EXISTS episodes (
            position_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, signal_day TEXT NOT NULL,
            payload TEXT NOT NULL, outcomes TEXT, complete INTEGER NOT NULL DEFAULT 0,
            error TEXT, updated_at TEXT);
    """)
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Exit research rules changed; start a new versioned research database")
    db.commit()
    return db


def review(snapshot, risk):
    """Classify all triggers, with a fixed risk limit taking precedence."""
    categories = []
    if snapshot.get("stop_loss_triggered"):
        categories.append("dynamic_stop")
    for field, label in (("ma20_break_triggered", "trend"), ("time_stop_triggered", "time"),
                         ("giveback_triggered", "giveback"), ("take_profit_triggered", "profit")):
        if snapshot.get(field):
            categories.append(label)
    valid = snapshot.get("close") and snapshot.get("return_pct") is not None
    if snapshot.get("market_date") and snapshot.get("checked_at") and snapshot["market_date"] < snapshot["checked_at"][:10]:
        valid = False
    warning = "종목 경고" in (snapshot.get("reasons") or "")
    hard = bool(valid and snapshot["return_pct"] <= -risk) or warning
    if hard:
        categories.insert(0, "risk_limit" if not warning else "stock_warning")
    distance, atr = snapshot.get("distance_ma20_pct"), snapshot.get("atr20_pct")
    near_trend = distance is not None and atr is not None and atr > 0 and distance >= -atr * 0.5
    decision = "HOLD"
    if not valid:
        decision = "DATA_REQUIRED"
    elif hard:
        decision = "SELL"
    elif snapshot.get("decision") == "SELL":
        if categories and set(categories) <= {"trend", "time"} and near_trend:
            decision = "DEFER_ONE_SESSION"
        elif "profit" in categories:
            decision = "PARTIAL_AND_TRAIL"
        else:
            decision = "SELL"
    return {"version": VERSION, "scope": "domestic_aggressive_observation", "categories": categories,
            "candidate_decision": decision, "risk_limit_pct": risk,
            "risk_basis": "first_observed_snapshot_not_entry", "max_extensions": 1,
            "entry_thesis": "unavailable", "market_relative_strength": "unavailable",
            "snapshot": dict(snapshot), "rules": RULES}


def harvest(source=DB_PATH, path=PATH):
    with closing(connect(path)) as db:
        activated = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        # Replay is idempotent; no historical event is silently relabelled as prospective.
        rows = query_rows("""SELECT p.*, r.market_date FROM position_checks p
            JOIN strategy_runs r ON r.run_id=p.run_id
            WHERE p.checked_at >= ? AND p.decision IN ('SELL','HOLD','NO_DATA')
            ORDER BY p.checked_at,p.run_id,p.position_id""", (activated,), path=source)
        added = 0
        for row in rows:
            key = row["position_id"]
            if db.execute("SELECT 1 FROM reviews WHERE run_id=? AND position_id=?", (row["run_id"], key)).fetchone():
                continue
            if row.get("atr20_pct") is not None and row.get("close") and row.get("market_date") == row["checked_at"][:10]:
                limit = min(RULES["risk_cap_pct"], max(RULES["risk_floor_pct"], row["atr20_pct"] * RULES["atr_multiple"]))
                db.execute("INSERT OR IGNORE INTO risks VALUES (?,?,?)", (key, row["checked_at"], limit))
            frozen = db.execute("SELECT limit_pct FROM risks WHERE position_id=?", (key,)).fetchone()
            result = review(row, frozen[0] if frozen else RULES["risk_floor_pct"])
            payload = json.dumps(result, ensure_ascii=False)
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?)", (row["run_id"], key, row["checked_at"], result["candidate_decision"], payload))
            # One independent episode per entry, not one sample every five minutes.
            if (row["decision"] == "SELL" or result["candidate_decision"] == "SELL") and result["candidate_decision"] != "DATA_REQUIRED":
                signal_day = max(row["checked_at"][:10], row.get("market_date") or "")
                db.execute("INSERT OR IGNORE INTO episodes(position_id,ticker,signal_day,payload) VALUES (?,?,?,?)",
                           (key, row["ticker"], signal_day, payload))
            added += 1
        db.commit()
    return added


def simulate(payload, bars):
    """Daily close signals execute at the NEXT session open, including gaps.

    Returns apply to the same hypothetical position sleeve; fractional units
    isolate exit timing and are not executable integer-share orders.
    """
    if not bars:
        return {}
    if any(len(b) < 6 or any(float(b[i]) <= 0 for i in (1, 2, 3, 4)) for b in bars):
        raise ValueError("Missing/invalid OHLC prices")
    initial = float(bars[0][1])
    snapshot = payload["snapshot"]
    risk_price = float(snapshot["entry_price"]) * (1 - payload["risk_limit_pct"] / 100)
    baseline_fraction = (RULES["partial_fraction"] if snapshot.get("sale_type") == "partial" else 1.0) if snapshot["decision"] == "SELL" else 0.0
    # Existing partial exits may use a configurable ratio: unknown ratio is
    # not reconstructed as fact, so this is explicitly a standardized benchmark.
    fraction = (RULES["partial_fraction"] if payload["candidate_decision"] == "PARTIAL_AND_TRAIL"
                else 1.0 if payload["candidate_decision"] == "SELL" else baseline_fraction)
    delay = 1 if payload["candidate_decision"] == "DEFER_ONE_SESSION" else 0
    fee = RULES["sell_cost_bps"] / 10000
    trail = max(RULES["trail_floor_pct"], float(snapshot.get("atr20_pct") or 0) * RULES["atr_multiple"])
    units, cash, pending, peak = 1.0, 0.0, False, initial
    result = {}
    for i, bar in enumerate(bars[:max(RULES["horizons"])]):
        opening, high, low, close = map(float, bar[1:5])
        if pending:
            cash += units * opening / initial * (1 - fee)
            units = 0.0
        elif i == delay:
            cash += fraction * opening / initial * (1 - fee)
            units -= fraction
        peak = max(peak, close)
        if units and (close <= risk_price or (payload["candidate_decision"] == "PARTIAL_AND_TRAIL" and close <= peak * (1 - trail / 100))):
            pending = True
        horizon = i + 1
        if horizon in RULES["horizons"]:
            candidate = (cash + units * close / initial - 1) * 100
            baseline = (baseline_fraction * (1 - fee) + (1 - baseline_fraction) * close / initial - 1) * 100
            window = bars[:horizon]
            result[str(horizon)] = {"baseline_return_pct": baseline, "candidate_return_pct": candidate,
                "difference_pct": candidate - baseline,
                "underlying_mfe_pct": (max(float(b[2]) for b in window) / initial - 1) * 100,
                "underlying_mae_pct": (min(float(b[3]) for b in window) / initial - 1) * 100,
                "remaining_fraction": units, "pending_exit": pending and units > 0,
                "as_of": str(bar[0]), "benchmark": "standardized_exit_sleeve_not_actual_account"}
    return result


def run(source=DB_PATH, path=PATH, today=None):
    from .app import naver_rows
    added = harvest(source, path)
    # Only completed sessions; conservative one-day delay avoids intraday bars.
    cutoff = (today or date.today()) - timedelta(days=1)
    failures, updated = 0, 0
    with closing(connect(path)) as db:
        for episode in db.execute("SELECT * FROM episodes WHERE complete=0").fetchall():
            try:
                signal = date.fromisoformat(episode["signal_day"])
                if cutoff <= signal:
                    continue
                rows = naver_rows(episode["ticker"], signal, cutoff)
                bars = sorted((r for r in rows if signal.strftime('%Y%m%d') < str(r[0]) <= cutoff.strftime('%Y%m%d')), key=lambda r: str(r[0]))
                if len({str(r[0]) for r in bars}) != len(bars):
                    raise ValueError("Duplicate market dates")
                outcomes = simulate(json.loads(episode["payload"]), bars)
                db.execute("UPDATE episodes SET outcomes=?,complete=?,error=NULL,updated_at=? WHERE position_id=?",
                           (json.dumps(outcomes), int("10" in outcomes), datetime.now().isoformat(timespec="seconds"), episode["position_id"]))
                updated += 1
            except Exception as error:
                failures += 1
                db.execute("UPDATE episodes SET error=?,updated_at=? WHERE position_id=?",
                           (str(error), datetime.now().isoformat(timespec="seconds"), episode["position_id"]))
        db.commit()
        counts = dict(db.execute("SELECT 'reviews',count(*) FROM reviews UNION ALL SELECT 'episodes',count(*) FROM episodes"))
    return {"version": VERSION, "added": added, "updated": updated, "failures": failures, **counts}


def dashboard_section(path=PATH):
    """Read-only view, including missing and immature observations."""
    episodes = query_rows("SELECT * FROM episodes ORDER BY signal_day DESC,position_id LIMIT 20", path=path)
    counts = query_rows("SELECT count(*) AS total,sum(complete) AS complete,sum(error IS NOT NULL) AS errors FROM episodes", path=path)
    counts = counts[0] if counts else {}
    meta = query_rows("SELECT value FROM metadata WHERE key='activated_at'", path=path)
    start = meta[0]["value"] if meta else "수집 준비 중"
    labels = {"SELL": "전량매도", "DEFER_ONE_SESSION": "1거래일 보유 연장", "PARTIAL_AND_TRAIL": "절반 익절·잔여 추적"}
    rows = []
    for episode in episodes:
        payload = json.loads(episode["payload"])
        outcomes = json.loads(episode["outcomes"] or "{}")
        cells = [episode["signal_day"], payload["snapshot"].get("name") or episode["ticker"],
                 labels.get(payload["candidate_decision"], payload["candidate_decision"])]
        cells += [f"{outcomes[str(h)]['difference_pct']:+.2f}%p" if str(h) in outcomes else "관찰 중" for h in RULES["horizons"]]
        cells += ["수집 오류" if episode["error"] else "10거래일 완료" if episode["complete"] else "관찰 중"]
        rows.append("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>")
    heads = ["판단일", "종목", "비교 판단", "1일 차이", "3일 차이", "5일 차이", "10일 차이", "상태"]
    body = ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            if rows else "<p class='muted'>관찰 시작 이후 비교할 매도 사례가 아직 없습니다.</p>")
    return ("<section><div class='table-heading'><h2>매도 전 재분석 관찰</h2>"
            f"<span class='table-count'>전체 {counts.get('total', 0)}건 · 10거래일 완료 {counts.get('complete') or 0}건 · 오류 {counts.get('errors') or 0}건</span></div>"
            f"<p class='muted'>시작 {escape(start)} · 국내 적극투자형 · 관찰 전용 · 최근 20건</p>"
            "<p class='muted'>기존 매도 신호에 맞춘 표준 비교 대비 차이입니다. 부분매도는 50%, 매도비용은 0.3%로 가정하며 실제 계좌 수익률이 아닙니다. "
            "양수는 비교안이 유리했다는 뜻입니다. 최초 매수 근거·시장 대비 강도는 아직 확인 불가이며, 미완료 기간은 성과에 포함하지 않습니다.</p>"
            + body + "</section>")


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
