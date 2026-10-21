"""Forward-only, stop-only comparisons anchored to actual virtual buys."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/risk_research.db"
VERSION = "actual-buy-risk-v1"
RULES = {"risk_budget": 0.005, "wide_stop_pct": 10.0, "stop_floor_pct": 5.0,
         "atr_multiple": 2.0, "fee_bps_each_side": 15, "horizons": [1, 3, 5, 10]}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cases(case_id TEXT PRIMARY KEY,ticker TEXT NOT NULL,
            bought_at TEXT NOT NULL,payload TEXT NOT NULL,outcomes TEXT,
            complete INTEGER NOT NULL DEFAULT 0,error TEXT,updated_at TEXT);""")
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Risk research rules changed; start a new versioned database")
    db.commit()
    return db


def plan(attempt, trade):
    """Freeze only evidence available when the virtual buy was observed."""
    evidence = attempt.get("evidence", {}).get(trade["ticker"], {})
    state = attempt.get("state", {})
    equity = float(state.get("total_equity") or 0)
    atr = evidence.get("atr20_pct")
    stop_rules = attempt.get("stop_rules") or {}
    price, quantity = float(trade.get("price") or 0), int(trade.get("quantity") or 0)
    if (not atr or not math.isfinite(float(atr)) or float(atr) <= 0 or equity <= 0 or
            not stop_rules or not math.isfinite(float(stop_rules.get("floor", 0))) or
            not math.isfinite(float(stop_rules.get("atr_multiplier", 0))) or
            float(stop_rules["floor"]) <= 0 or float(stop_rules["atr_multiplier"]) <= 0 or
            price <= 0 or quantity <= 0 or evidence.get("evaluated_at", "") > attempt["observed_at"] or
            evidence.get("evaluated_at", "")[:10] != attempt["observed_at"][:10] or
            any(h.get("ticker") == trade["ticker"] for h in state.get("holdings", []))):
        return None
    stop_pct = max(float(stop_rules["floor"]), float(atr) * float(stop_rules["atr_multiplier"]))
    sized_quantity = min(quantity, int(equity * RULES["risk_budget"] / (price * stop_pct / 100)))
    return {"version": VERSION, "rules": RULES, "ticker": trade["ticker"],
            "name": trade.get("name") or trade["ticker"], "bought_at": attempt["observed_at"],
            "price": price, "quantity": quantity, "equity": equity,
            "atr20_pct": float(atr), "entry_stop_pct": stop_pct,
            "risk_sized_quantity": sized_quantity,
            "skip_wide_stop": stop_pct > RULES["wide_stop_pct"],
            "source_attempt_id": attempt.get("attempt_id")}


def harvest(source="data/portfolio_research.db", path=PATH):
    with closing(connect(path)) as db:
        start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        rows = query_rows("SELECT * FROM buy_attempts WHERE profile='aggressive' AND error IS NULL AND observed_at>=? ORDER BY observed_at,attempt_id", (start,), path=source)
        added = 0
        for row in rows:
            attempt = json.loads(row["payload"])
            attempt["attempt_id"] = row["attempt_id"]
            for trade in attempt.get("executions", []):
                payload = plan(attempt, trade)
                if payload:
                    added += db.execute("INSERT OR IGNORE INTO cases(case_id,ticker,bought_at,payload) VALUES (?,?,?,?)",
                                        (f"{row['attempt_id']}:{trade['ticker']}", trade["ticker"], row["observed_at"], json.dumps(payload, ensure_ascii=False))).rowcount
        db.commit()
    return added


def simulate(payload, bars, dynamic_stops):
    """Daily close stop signals sell at next open; absent live checks stay unknown."""
    price, equity = payload["price"], payload["equity"]
    fee = RULES["fee_bps_each_side"] / 10000
    variants = {"current": payload["quantity"], "risk_sized": payload["risk_sized_quantity"],
                "skip_wide": 0 if payload["skip_wide_stop"] else payload["quantity"],
                "fixed_entry_stop": payload["quantity"]}
    states = {key: {"qty": qty, "cash": 0.0, "pending": False, "exit_price": None,
                    "trigger_price": None, "overshoot_pct": None}
              for key, qty in variants.items()}
    results = {}
    for i, bar in enumerate(bars[:max(RULES["horizons"])]):
        day = str(bar[0])
        opening, high, low, close = map(float, bar[1:5])
        if any(not math.isfinite(v) or v <= 0 for v in (opening, high, low, close)) or high < max(opening, close) or low > min(opening, close):
            raise ValueError("Invalid OHLC")
        dynamic = dynamic_stops.get(day)
        if dynamic is not None and (not math.isfinite(float(dynamic)) or float(dynamic) <= 0):
            raise ValueError("Invalid observed dynamic stop")
        for key, state in states.items():
            if state["pending"] and state["qty"]:
                state["cash"] += state["qty"] * opening * (1 - fee)
                state["exit_price"] = opening
                state["overshoot_pct"] = (state["trigger_price"] - opening) / state["trigger_price"] * 100
                state["qty"] = 0
            if not state["qty"]:
                continue
            stop = payload["entry_stop_pct"] if key == "fixed_entry_stop" else dynamic
            if stop is None:
                if key != "fixed_entry_stop":
                    state["unknown"] = True
                continue
            if close <= price * (1 - float(stop) / 100):
                state["pending"] = True
                state["trigger_price"] = price * (1 - float(stop) / 100)
        if i + 1 in RULES["horizons"]:
            contributions = {}
            for key, state in states.items():
                if state.get("unknown"):
                    contributions[key] = None
                    continue
                value = state["cash"] + state["qty"] * close * (1 - fee)
                contributions[key] = (value - variants[key] * price * (1 + fee)) / equity * 100
            base = contributions["current"]
            results[str(i + 1)] = {"as_of": day, "contribution_pct": contributions,
                "difference_pct": {key: (value - base if value is not None and base is not None else None)
                                   for key, value in contributions.items() if key != "current"},
                "mae_pct": (min(float(b[3]) for b in bars[:i + 1]) / price - 1) * 100,
                "mfe_pct": (max(float(b[2]) for b in bars[:i + 1]) / price - 1) * 100,
                "exit_price": {key: state["exit_price"] for key, state in states.items()},
                "post_exit_return_pct": {key: (close / state["exit_price"] - 1) * 100 if state["exit_price"] else None
                                         for key, state in states.items()},
                "stop_overshoot_pct": {key: state["overshoot_pct"] for key, state in states.items()},
                "dynamic_stop_pct": dynamic}
    return results


def run(source=DB_PATH, attempts="data/portfolio_research.db", path=PATH, today=None):
    from .app import naver_rows
    added = harvest(attempts, path)
    cutoff = (today or date.today()) - timedelta(days=1)
    updated = failures = 0
    with closing(connect(path)) as db:
        for case in db.execute("SELECT * FROM cases WHERE complete=0").fetchall():
            bought = date.fromisoformat(case["bought_at"][:10])
            if cutoff <= bought:
                continue
            try:
                history = naver_rows(case["ticker"], bought, cutoff)
                bars = sorted((r for r in history if bought.strftime("%Y%m%d") < str(r[0]) <= cutoff.strftime("%Y%m%d")), key=lambda r: str(r[0]))
                if len({str(r[0]) for r in bars}) != len(bars):
                    raise ValueError("Duplicate market dates")
                checks = query_rows("SELECT p.checked_at,p.dynamic_stop_loss_pct FROM position_checks p JOIN strategy_runs r USING(run_id) WHERE p.ticker=? AND p.checked_at>? AND p.checked_at<? AND r.market_date=substr(p.checked_at,1,10) ORDER BY p.checked_at", (case["ticker"], case["bought_at"], cutoff.isoformat() + "T23:59:59"), path=source)
                dynamic = {row["checked_at"][:10].replace("-", ""): abs(float(row["dynamic_stop_loss_pct"]))
                           for row in checks if row["dynamic_stop_loss_pct"] is not None}
                results = simulate(json.loads(case["payload"]), bars, dynamic)
                complete = int(str(max(RULES["horizons"])) in results)
                db.execute("UPDATE cases SET outcomes=?,complete=?,error=NULL,updated_at=? WHERE case_id=?",
                           (json.dumps(results), complete, datetime.now().isoformat(timespec="seconds"), case["case_id"]))
                updated += 1
            except Exception as error:
                failures += 1
                db.execute("UPDATE cases SET error=?,updated_at=? WHERE case_id=?",
                           (str(error), datetime.now().isoformat(timespec="seconds"), case["case_id"]))
        db.commit()
        total = db.execute("SELECT count(*) FROM cases").fetchone()[0]
    return {"version": VERSION, "added": added, "updated": updated, "failures": failures, "cases": total}


def dashboard_section(path=PATH):
    cases = query_rows("SELECT * FROM cases ORDER BY bought_at DESC LIMIT 20", path=path)
    cells = []
    for row in cases:
        payload = json.loads(row["payload"])
        outcome = json.loads(row["outcomes"] or "{}").get("10", {})
        differences = outcome.get("difference_pct", {})
        fmt = lambda key: f"{differences[key]:+.2f}%p" if differences.get(key) is not None else "자료 부족" if outcome else "관찰 중"
        values = [row["bought_at"].replace("T", " ")[:19], payload["name"], f"{payload['entry_stop_pct']:.1f}%",
                  f"{payload['quantity']} → {payload['risk_sized_quantity']}주",
                  "보류" if payload["skip_wide_stop"] else "매수", fmt("risk_sized"), fmt("skip_wide"), fmt("fixed_entry_stop"),
                  "오류" if row["error"] else "10일 경과·자료 부족" if row["complete"] and differences.get("risk_sized") is None
                  else "10일 완료" if row["complete"] else "관찰 중"]
        cells.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>")
    heads = ["실제 매수 시각", "종목", "진입 손절폭", "실제→위험조절", "10% 초과안", "비중안 차이", "보류안 차이", "손절 고정안 차이", "상태"]
    body = ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(cells) + "</tbody></table>"
            if cells else "<p class='muted'>활성화 이후 실제 가상매수 체결을 기다리고 있습니다.</p>")
    return ("<section><h2>실제 매수 위험·손절 관찰</h2><p class='muted'>국내 적극투자형 신규 가상매수만 비교합니다. "
            "계좌 위험 0.5% 비중·손절폭 10% 초과 진입 보류·진입 시 손절선 고정을 기존 변동 손절과 비교합니다. "
            "같은 체결가와 이후 일봉을 사용하며 손절 신호 다음 거래일 시가·왕복 비용 0.3%를 가정합니다. "
            "표의 차이는 단일 거래의 계좌 기여도이지 실제 계좌 수익률이 아닙니다. 현재 기준은 손절만 표준화한 비교로 다른 매도 사유는 반영하지 않습니다. "
            "매수 당일은 일봉에 매수 전 가격이 섞여 제외합니다. 해당 일자의 손절 점검 기록이 없으면 성과를 자료 부족으로 표시합니다.</p>" + body + "</section>")


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
