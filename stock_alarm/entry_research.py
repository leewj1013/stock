"""Prospective entry/sizing hypotheses; no changes to orders or frozen engines."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/entry_research.db"
VERSION = "entry-observation-v1"
RULES = {"baseline_weight": 0.10, "risk_budget": 0.005, "stop_floor_pct": 5,
         "stop_cap_pct": 10, "atr_multiple": 2, "stretch_atr": 2,
         "fee_bps_each_side": 15, "horizons": [1, 3, 5, 10], "planned_sessions": 10}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(
            ticker TEXT NOT NULL, signal_day TEXT NOT NULL, observed_at TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL, outcomes TEXT, error TEXT,
            updated_at TEXT, PRIMARY KEY(ticker,signal_day));
    """)
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Entry research rules changed; create a new versioned database")
    db.commit()
    return db


def plan(row):
    """Use only the immutable recommendation-time evidence, never later prices."""
    missing = []
    for key in ("close", "atr20_pct", "distance_ma20_pct"):
        value = row.get(key)
        if value is None or not math.isfinite(float(value)) or (key != "distance_ma20_pct" and float(value) <= 0):
            missing.append(key)
    if row.get("market_date") != row.get("evaluated_at", "")[:10]:
        missing.append("fresh_market_date")
    payload = {"version": VERSION, "rules": RULES, "evidence": dict(row),
               "planned_sessions": RULES["planned_sessions"], "missing": missing,
               "thesis_basis": "feature_classification_not_original_buy_reason"}
    if missing:
        return {**payload, "state": "DATA_REQUIRED", "thesis": "unclassified"}
    atr, distance = float(row["atr20_pct"]), float(row["distance_ma20_pct"])
    risk = min(RULES["stop_cap_pct"], max(RULES["stop_floor_pct"], atr * RULES["atr_multiple"]))
    stretched = distance / atr >= RULES["stretch_atr"]
    thesis = ("trend_volume" if distance >= 0 and float(row.get("volume_ratio") or 0) >= 2
              else "trend" if distance >= 0 else "below_ma20")
    return {**payload, "state": "PENDING", "thesis": thesis, "stretched": stretched,
            "risk_limit_pct": risk, "invalidation": "daily_close_below_fixed_entry_stop",
            "weights": {"baseline": RULES["baseline_weight"],
                        "avoid_stretched": 0.0 if stretched else RULES["baseline_weight"],
                        "risk_sized": min(RULES["baseline_weight"], RULES["risk_budget"] / (risk / 100))}}


def harvest(source=DB_PATH, path=PATH):
    with closing(connect(path)) as db:
        start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        rows = query_rows("""SELECT c.*,r.market_date,r.strategy_version FROM candidate_snapshots c
            JOIN strategy_runs r ON r.run_id=c.run_id WHERE c.selected=1 AND c.evaluated_at>=?
            ORDER BY c.evaluated_at,c.run_id,c.ticker""", (start,), path=source)
        added = 0
        for row in rows:
            payload = plan(row)
            day = row["evaluated_at"][:10]
            added += db.execute("INSERT OR IGNORE INTO entries(ticker,signal_day,observed_at,payload,state) VALUES (?,?,?,?,?)",
                                (row["ticker"], day, row["evaluated_at"], json.dumps(payload, ensure_ascii=False), payload["state"])).rowcount
        db.commit()
    return added


def simulate(payload, bars):
    if payload["state"] == "DATA_REQUIRED" or not bars:
        return {}
    for b in bars:
        values = [float(v) for v in b[1:5]]
        if len(values) != 4 or any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Invalid OHLC price")
        opening, high, low, close = values
        if high < max(opening, close) or low > min(opening, close) or low > high:
            raise ValueError("Inconsistent OHLC price")
    entry = float(bars[0][1])
    stop = entry * (1 - payload["risk_limit_pct"] / 100)
    fee = RULES["fee_bps_each_side"] / 10000
    cash, held, pending, exit_day, exit_price = 0.0, True, False, None, None
    outcomes = {}
    for i, bar in enumerate(bars[:RULES["planned_sessions"]]):
        opening, high, low, close = map(float, bar[1:5])
        if pending and held:
            cash, held = opening / entry * (1 - fee), False
            exit_day, exit_price = str(bar[0]), opening
        if held and close <= stop:
            pending = True
        horizon = i + 1
        if horizon in RULES["horizons"]:
            # Net liquidation value at the horizon; no claim of an actual close fill.
            value = close / entry * (1 - fee) if held else cash
            net = (value / (1 + fee) - 1) * 100
            window = bars[:horizon]
            mfe = (max(float(r[2]) for r in window) / entry - 1) * 100
            mae = (min(float(r[3]) for r in window) / entry - 1) * 100
            gross = (close / entry - 1) * 100
            contributions = {k: w * net for k, w in payload["weights"].items()}
            outcomes[str(horizon)] = {"stock_net_return_pct": net, "contribution_pct": contributions,
                "difference_pct": {k: v - contributions["baseline"] for k, v in contributions.items() if k != "baseline"},
                "mfe_pct": mfe, "mae_pct": mae, "giveback_pct_points": max(0, mfe - gross),
                "first_session_return_pct": (float(bars[0][4]) / entry - 1) * 100,
                "exit_day": exit_day, "exit_price": exit_price,
                "post_exit_return_pct": (close / exit_price - 1) * 100 if exit_price else None,
                "pending_stop": held and pending, "as_of": str(bar[0])}
    return {"execution_day": str(bars[0][0]), "entry_price": entry, "fixed_stop_price": stop, "horizons": outcomes}


def run(source=DB_PATH, path=PATH, today=None):
    from .app import naver_rows
    added = harvest(source, path)
    cutoff = (today or date.today()) - timedelta(days=1)
    failures, updated = 0, 0
    with closing(connect(path)) as db:
        for row in db.execute("SELECT * FROM entries WHERE state='PENDING'").fetchall():
            signal = date.fromisoformat(row["signal_day"])
            if cutoff <= signal:
                continue
            try:
                history = naver_rows(row["ticker"], signal, cutoff)
                bars = sorted((r for r in history if signal.strftime('%Y%m%d') < str(r[0]) <= cutoff.strftime('%Y%m%d')), key=lambda r: str(r[0]))
                if len({str(r[0]) for r in bars}) != len(bars):
                    raise ValueError("Duplicate market dates")
                results = simulate(json.loads(row["payload"]), bars)
                state = "COMPLETE" if "10" in results.get("horizons", {}) else "PENDING"
                db.execute("UPDATE entries SET outcomes=?,state=?,error=NULL,updated_at=? WHERE ticker=? AND signal_day=?",
                           (json.dumps(results), state, datetime.now().isoformat(timespec="seconds"), row["ticker"], row["signal_day"]))
                updated += 1
            except Exception as error:
                failures += 1
                db.execute("UPDATE entries SET error=?,updated_at=? WHERE ticker=? AND signal_day=?",
                           (str(error), datetime.now().isoformat(timespec="seconds"), row["ticker"], row["signal_day"]))
        db.commit()
        total = db.execute("SELECT count(*) FROM entries").fetchone()[0]
    return {"version": VERSION, "added": added, "updated": updated, "failures": failures, "entries": total}


def dashboard_section(path=PATH):
    rows = query_rows("SELECT * FROM entries ORDER BY observed_at DESC,ticker LIMIT 20", path=path)
    counts = query_rows("SELECT count(*) AS total,sum(state='COMPLETE') AS complete,sum(state='DATA_REQUIRED') AS missing,sum(error IS NOT NULL) AS errors FROM entries", path=path)
    counts = counts[0] if counts else {}
    labels = {"trend_volume": "추세·거래량", "trend": "추세", "below_ma20": "20일선 아래", "unclassified": "자료 부족"}
    rendered = []
    diagnostics = []
    for row in rows:
        p = json.loads(row["payload"])
        horizons = json.loads(row["outcomes"] or "{}").get("horizons", {})
        outcome = horizons.get("10")
        cells = [row["observed_at"].replace("T", " "), p["evidence"].get("name") or row["ticker"], labels[p["thesis"]],
                 "자료 부족" if p["state"] == "DATA_REQUIRED" else "보류" if p["stretched"] else "매수",
                 f"{p['weights']['risk_sized'] * 100:.1f}%" if "weights" in p else "확인 불가"]
        cells += [f"{outcome['difference_pct'][k]:+.3f}%p" if outcome else "관찰 중" for k in ("avoid_stretched", "risk_sized")]
        cells += ["수집 오류" if row["error"] else "자료 부족" if row["state"] == "DATA_REQUIRED" else "완료" if row["state"] == "COMPLETE" else "관찰 중"]
        rendered.append("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>")
        available = max((int(h) for h in horizons), default=0)
        last = horizons.get(str(available), {})
        values = [p["evidence"].get("name") or row["ticker"], row["signal_day"],
                  f"{p['risk_limit_pct']:.1f}%" if 'risk_limit_pct' in p else "확인 불가",
                  f"{available}거래일" if available else "관찰 중"]
        values += [f"{last[key]:+.2f}%" if last.get(key) is not None else "확인 불가"
                   for key in ("first_session_return_pct", "mfe_pct", "mae_pct", "post_exit_return_pct")]
        diagnostics.append("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in values) + "</tr>")
    heads = ["추천 시각", "종목", "관측 유형", "가격 부담안", "위험 조절 비중", "가격 부담안 차이", "비중 조절안 차이", "상태"]
    table = ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(rendered) + "</tbody></table>"
             if rows else "<p class='muted'>관찰 시작 이후 새 추천을 기다리고 있습니다.</p>")
    if diagnostics:
        titles = ["종목", "추천일", "고정 손절폭", "진단 기간", "첫날 등락", "최대 상승폭", "최대 하락폭", "모의 손절 후 등락"]
        table += ("<details><summary>진입·손절 진단 보기</summary><div class='details-body'><p class='muted'>"
                  "기초종목 가격 기준입니다. 최대 상승·하락폭은 가상 매도 이후도 포함하며, 손절 전에는 손절 후 등락을 계산하지 않습니다.</p><table><thead><tr>"
                  + "".join(f"<th>{h}</th>" for h in titles) + "</tr></thead><tbody>" + "".join(diagnostics) + "</tbody></table></div></details>")
    return ("<section><div class='table-heading'><h2>매수 근거·진입 가격·비중 관찰</h2>"
            f"<span class='table-count'>전체 {counts.get('total', 0)}건 · 완료 {counts.get('complete') or 0}건 · 자료 부족 {counts.get('missing') or 0}건 · 오류 {counts.get('errors') or 0}건</span></div>"
            "<p class='muted'>국내 적극투자형 추천 · 최근 20건 · 다음 거래일 시가 가정 · 10거래일 비교. "
            "같은 매도 기준에서 10% 비중 매수 대비 가격 부담 시 보류·위험에 따른 비중 축소를 비교합니다.</p>"
            "<p class='muted'>차이는 단일 종목의 계좌 기여도이며 합산 계좌 수익률이 아닙니다. 관측 유형은 추천 당시 지표로 분류한 가설입니다. "
            "서로 겹치는 추천 기간이 있어 사례 수를 독립 표본 수로 해석하지 않습니다.</p>" + table + "</section>")


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
