"""Prospective earnings-event experiments; never changes live orders."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/earnings_research.db"
VERSION = "earnings-observation-v1"
RULES = {"scan_days": 45, "gap_chase_limit_pct": 12, "locked_limit_pct": 29.5,
         "fee_bps_each_side": 15, "horizons": [1, 3, 5, 10, 20],
         "strong_revenue_growth_pct": 10, "strong_operating_growth_pct": 20,
         "weak_revenue_growth_pct": -10, "weak_operating_growth_pct": -30}
REPORT_CODES = {"분기보고서": "11013", "반기보고서": "11012", "사업보고서": "11011"}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
            receipt_no TEXT PRIMARY KEY,ticker TEXT NOT NULL,name TEXT,event_day TEXT NOT NULL,
            observed_at TEXT NOT NULL,event_type TEXT NOT NULL,title TEXT NOT NULL,
            time_precision TEXT NOT NULL,metrics TEXT NOT NULL,severity TEXT NOT NULL,
            state TEXT NOT NULL,outcomes TEXT,error TEXT,updated_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_earnings_event_day ON events(event_day,ticker);
    """)
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Earnings research rules changed; create a new versioned database")
    db.commit()
    return db


def event_kind(title: str) -> tuple[str, str | None]:
    correction = "정정" in title
    if "잠정" in title or "매출액또는손익구조" in title.replace(" ", ""):
        return ("provisional_correction" if correction else "provisional"), None
    for label, code in REPORT_CODES.items():
        if label in title:
            return ("periodic_correction" if correction else "periodic"), code
    return "", None


def metrics_from_summary(summary: dict) -> dict:
    def growth(current, previous):
        return (current - previous) / abs(previous) * 100 if current is not None and previous else None

    def margin(value, revenue):
        return value / revenue * 100 if value is not None and revenue else None

    revenue, prior_revenue = summary.get("revenue"), summary.get("revenue_prev")
    operating, prior_operating = summary.get("operating_income"), summary.get("operating_income_prev")
    current_margin, prior_margin = margin(operating, revenue), margin(prior_operating, prior_revenue)
    return {"revenue": revenue, "operating_income": operating, "net_income": summary.get("net_income"),
            "revenue_growth_pct": growth(revenue, prior_revenue),
            "operating_income_growth_pct": growth(operating, prior_operating),
            "operating_margin_pct": current_margin,
            "operating_margin_change_pctp": current_margin - prior_margin if current_margin is not None and prior_margin is not None else None,
            "operating_cash_flow": None, "consensus_surprise_pct": None,
            "source": "DART periodic accounts", "business_year": summary.get("bsns_year"),
            "report_code": summary.get("reprt_code")}


def classify(metrics: dict) -> str:
    revenue, operating = metrics.get("revenue_growth_pct"), metrics.get("operating_income_growth_pct")
    income, margin_change = metrics.get("operating_income"), metrics.get("operating_margin_change_pctp")
    if revenue is None or operating is None or income is None:
        return "unclassified"
    if income <= 0 or operating <= RULES["weak_operating_growth_pct"] or revenue <= RULES["weak_revenue_growth_pct"]:
        return "strong_negative"
    if revenue >= RULES["strong_revenue_growth_pct"] and operating >= RULES["strong_operating_growth_pct"] and (margin_change or 0) >= 0:
        return "strong_positive"
    if revenue < 0 or operating < 0 or (margin_change is not None and margin_change < 0):
        return "negative"
    return "neutral"


def _year(title: str, event_day: date) -> int:
    found = re.search(r"(20\d{2})", title)
    return int(found.group(1)) if found else event_day.year


def universe(source=DB_PATH) -> list[dict]:
    return query_rows("""SELECT ticker,MAX(COALESCE(name,ticker)) AS name FROM (
        SELECT ticker,name FROM candidate_snapshots WHERE selected=1 AND run_id=(
            SELECT run_id FROM strategy_runs WHERE run_type='recommendation' AND status='completed'
            ORDER BY started_at DESC LIMIT 1)
        UNION ALL SELECT ticker,name FROM virtual_trades
        WHERE ticker IN (SELECT ticker FROM virtual_position_states WHERE status='open' AND remaining_quantity>0)
    ) GROUP BY ticker""", path=source)


def harvest(source=DB_PATH, path=PATH, disclosure_fetch=None, summary_fetch=None, now=None):
    from .dart_reference import disclosures
    from .financial_statement_reference import fetch_account_summary
    disclosure_fetch, summary_fetch = disclosure_fetch or disclosures, summary_fetch or fetch_account_summary
    now = now or datetime.now()
    added, failures = 0, 0
    with closing(connect(path)) as db:
        activated = datetime.fromisoformat(db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]).date()
        for stock in universe(source):
            try:
                filings = disclosure_fetch(stock["ticker"], RULES["scan_days"], 100)
            except Exception:
                failures += 1
                continue
            for filing in filings:
                title = str(filing.get("report_nm") or "")
                kind, report_code = event_kind(title)
                receipt_no, raw_day = str(filing.get("rcept_no") or ""), str(filing.get("rcept_dt") or "")
                if not kind or not receipt_no or len(raw_day) != 8:
                    continue
                event_day = date.fromisoformat(f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}")
                if event_day < activated or event_day > now.date():
                    continue
                metrics = {"source": "DART title only", "operating_cash_flow": None, "consensus_surprise_pct": None}
                error = None
                if report_code:
                    try:
                        metrics = metrics_from_summary(summary_fetch(stock["ticker"], _year(title, event_day), report_code))
                    except Exception as exc:
                        error = str(exc)
                        failures += 1
                severity = classify(metrics)
                state = "PENDING" if severity != "unclassified" else "DATA_REQUIRED"
                added += db.execute("""INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (receipt_no, stock["ticker"], stock.get("name"), event_day.isoformat(), now.isoformat(timespec="seconds"),
                     kind, title, "date_only_next_session_conservative", json.dumps(metrics, ensure_ascii=False), severity,
                     state, None, error, now.isoformat(timespec="seconds"))).rowcount
        db.commit()
    return added, failures


def _valid_bars(bars):
    ordered = sorted(bars, key=lambda row: str(row[0]))
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


def simulate(severity: str, bars: list[list]) -> dict:
    bars = _valid_bars(bars)
    if len(bars) < 2:
        return {}
    fee = RULES["fee_bps_each_side"] / 10000
    first, previous = bars[1], bars[0]
    previous_close = float(previous[4])
    gap = (float(first[1]) / previous_close - 1) * 100
    day_return = (float(first[4]) / previous_close - 1) * 100
    locked = day_return >= RULES["locked_limit_pct"] and float(first[2]) == float(first[3])
    outcomes = {"first_session": {"day": str(first[0]), "gap_pct": gap, "return_pct": day_return,
                                    "locked_limit_up": locked, "immediate_fillable": not locked}}
    entries = {"no_event_trade": None}
    if severity == "strong_positive":
        entries["immediate"] = None if locked else (1, float(first[1]))
        confirmed = None
        for index in range(1, len(bars) - 1):
            row, prior = bars[index], bars[index - 1]
            row_gap = (float(row[1]) / float(prior[4]) - 1) * 100
            row_locked = (float(row[4]) / float(prior[4]) - 1) * 100 >= RULES["locked_limit_pct"] and float(row[2]) == float(row[3])
            if not row_locked and row_gap < RULES["gap_chase_limit_pct"] and float(row[4]) >= float(row[1]):
                confirmed = (index + 1, float(bars[index + 1][1]))
                break
        entries["confirmed"] = confirmed
    elif severity in ("negative", "strong_negative"):
        entries = {"hold": (1, float(first[1])), "immediate_exit": None}
    horizon_rows = {}
    for horizon in RULES["horizons"]:
        index = horizon
        if index >= len(bars):
            continue
        close = float(bars[index][4])
        values = {}
        for variant, entry in entries.items():
            if variant in ("no_event_trade", "immediate_exit"):
                values[variant] = 0.0
            elif entry is None or entry[0] > index:
                values[variant] = None
            else:
                values[variant] = (close / entry[1] * (1 - fee) / (1 + fee) - 1) * 100
        horizon_rows[str(horizon)] = {"as_of": str(bars[index][0]), "return_pct": values}
    outcomes["horizons"] = horizon_rows
    return outcomes


def run(source=DB_PATH, path=PATH, today=None):
    from .app import naver_rows
    added, failures = harvest(source, path)
    cutoff = (today or date.today()) - timedelta(days=1)
    updated = 0
    with closing(connect(path)) as db:
        for event in db.execute("SELECT * FROM events WHERE state='PENDING'").fetchall():
            signal = date.fromisoformat(event["event_day"])
            if cutoff <= signal:
                continue
            try:
                history = naver_rows(event["ticker"], signal - timedelta(days=10), cutoff)
                signal_key, cutoff_key = signal.strftime("%Y%m%d"), cutoff.strftime("%Y%m%d")
                before = sorted((row for row in history if str(row[0]) <= signal_key), key=lambda row: str(row[0]))
                after = sorted((row for row in history if signal_key < str(row[0]) <= cutoff_key), key=lambda row: str(row[0]))
                bars = ([before[-1]] if before else []) + after
                result = simulate(event["severity"], bars)
                complete = "20" in result.get("horizons", {})
                db.execute("UPDATE events SET outcomes=?,state=?,error=NULL,updated_at=? WHERE receipt_no=?",
                           (json.dumps(result), "COMPLETE" if complete else "PENDING", datetime.now().isoformat(timespec="seconds"), event["receipt_no"]))
                updated += 1
            except Exception as exc:
                failures += 1
                db.execute("UPDATE events SET error=?,updated_at=? WHERE receipt_no=?", (str(exc), datetime.now().isoformat(timespec="seconds"), event["receipt_no"]))
            db.commit()
        total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"version": VERSION, "added": added, "updated": updated, "failures": failures, "events": total}


def dashboard_section(path=PATH):
    if not Path(path).exists():
        return "<section><h2>실적 발표 대응 관찰</h2><p class='muted'>관찰 DB 생성 전입니다.</p></section>"
    rows = query_rows("SELECT * FROM events ORDER BY event_day DESC,observed_at DESC LIMIT 20", path=path)
    counts = query_rows("SELECT COUNT(*) total,SUM(state='COMPLETE') complete,SUM(state='DATA_REQUIRED') missing,SUM(error IS NOT NULL) errors FROM events", path=path)[0]
    severity_labels = {"strong_positive": "강한 개선", "neutral": "중립", "negative": "악화", "strong_negative": "심각한 악화", "unclassified": "수치 확인 필요"}
    event_labels = {"provisional": "잠정실적", "provisional_correction": "잠정실적 정정",
                    "periodic": "정기보고서", "periodic_correction": "정기보고서 정정"}
    body = []
    for row in rows:
        metrics, outcome = json.loads(row["metrics"]), json.loads(row["outcomes"] or "{}")
        first = outcome.get("first_session", {})
        ten_day = outcome.get("horizons", {}).get("10", {}).get("return_pct", {})
        first_variant = ten_day.get("immediate") if row["severity"] == "strong_positive" else ten_day.get("hold")
        second_variant = ten_day.get("confirmed") if row["severity"] == "strong_positive" else ten_day.get("immediate_exit")
        values = [row["event_day"], row["name"] or row["ticker"], event_labels[row["event_type"]], severity_labels[row["severity"]],
                  f"{metrics['revenue_growth_pct']:+.1f}%" if metrics.get("revenue_growth_pct") is not None else "확인 불가",
                  f"{metrics['operating_income_growth_pct']:+.1f}%" if metrics.get("operating_income_growth_pct") is not None else "확인 불가",
                  "체결 곤란" if first.get("locked_limit_up") else "체결 가능" if first else "관찰 중",
                  f"{first_variant:+.2f}%" if first_variant is not None else "관찰 중",
                  f"{second_variant:+.2f}%" if second_variant is not None else "관찰 중",
                  "오류" if row["error"] else "완료" if row["state"] == "COMPLETE" else "수치 확인 필요" if row["state"] == "DATA_REQUIRED" else "관찰 중"]
        body.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>")
    heads = ["발표일", "종목", "발표 유형", "실적 분류", "매출 성장", "영업이익 성장", "첫 거래일", "10일 즉시·보유", "10일 확인·매도", "상태"]
    table = ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
             if rows else "<p class='muted'>관찰 시작 이후 실적 발표를 기다리고 있습니다.</p>")
    return ("<section><div class='table-heading'><h2>실적 발표 대응 관찰</h2>"
            f"<span class='table-count'>전체 {counts['total']}건 · 완료 {counts['complete'] or 0}건 · 수치 확인 필요 {counts['missing'] or 0}건 · 오류 {counts['errors'] or 0}건</span></div>"
            "<p class='muted'>실제 주문은 변경하지 않습니다. DART 접수일 다음 거래일부터 계산하며, 잠정실적에 구조화 수치가 없으면 임의로 좋음·나쁨을 판정하지 않습니다.</p>"
            "<p class='muted'>강한 개선은 즉시 진입과 갭 12% 미만·양봉 확인 후 진입을 비교하고, 악화는 보유와 다음 거래일 매도를 비교합니다. 예상치·현금흐름은 자료가 없어 현재 판정에 사용하지 않습니다.</p>" + table + "</section>")


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
