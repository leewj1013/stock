"""Forward-only observations for US entries; never changes strategy ledgers."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import urllib.request
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .us_strategy_account import ACCOUNTS, SRC

PATH = "data/us_research_entry_v2.db"
VERSION = "us-entry-observation-v2-prior-session"
MARKET_HISTORY = "data/us_market_history.csv"
COST = 0.0015
HORIZONS = (1, 3, 5, 10, 20)


def connect(path=PATH):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS episodes(
        account TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT, entry_day TEXT NOT NULL,
        shares INTEGER NOT NULL, entry_price REAL NOT NULL, prior_close REAL,
        gap_pct REAL, vix REAL, spy_close REAL, spy_ma20 REAL, defensive INTEGER NOT NULL,
        sec_form TEXT, sec_accepted_at TEXT, sec_accession TEXT, outcomes TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL, error TEXT, observed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(account,ticker,entry_day))""")
    db.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    row = db.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
    if row and row[0] != VERSION:
        raise ValueError("US research rules changed; use a new versioned database")
    db.execute("INSERT OR IGNORE INTO metadata VALUES('version',?)", (VERSION,))
    db.commit()
    return db


def _csv(path) -> list[dict[str, str]]:
    try:
        with open(path, encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))
    except OSError:
        return []


def _bars(ticker: str) -> list[dict[str, str]]:
    return _csv(SRC / f"{ticker}.csv")


def _ticker(symbol: str) -> str:
    value = symbol.split(".", 1)[0]
    if len(value) > 1 and value[-1:] in ("a", "b"):
        return value[:-1] + "-" + value[-1].upper()
    return value


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": os.environ.get("SEC_USER_AGENT", "stockAlarm/1.0 research")})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def sec_tickers(cache=Path(".cache/sec_company_tickers.json")) -> dict[str, str]:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists() or datetime.fromtimestamp(cache.stat().st_mtime).date() < date.today():
        cache.write_text(json.dumps(_get_json("https://www.sec.gov/files/company_tickers.json")), encoding="utf-8")
    rows = json.loads(cache.read_text(encoding="utf-8"))
    return {str(row["ticker"]).upper(): f"{int(row['cik_str']):010d}" for row in rows.values()}


def sec_event(ticker: str, entry_day: str, lookup=None) -> dict:
    """Return a confirmed earnings filing known no later than the entry session."""
    lookup = lookup or sec_tickers()
    cik = lookup.get(_ticker(ticker).upper())
    if not cik:
        return {}
    body = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = body.get("filings", {}).get("recent", {})
    entry = datetime.strptime(entry_day, "%Y-%m-%d")
    best = None
    for i, form in enumerate(recent.get("form", [])):
        accepted = str(recent.get("acceptanceDateTime", [""] * (i + 1))[i])
        filed = str(recent.get("filingDate", [""] * (i + 1))[i])
        items = str(recent.get("items", [""] * (i + 1))[i])
        confirmed = form in ("10-Q", "10-K") or (form == "8-K" and "2.02" in items)
        if not confirmed or not filed:
            continue
        filing_day = datetime.strptime(filed, "%Y-%m-%d")
        if timedelta(days=1) <= entry - filing_day <= timedelta(days=4):
            candidate = {"form": form, "accepted_at": accepted or filed,
                         "accession": recent.get("accessionNumber", [""] * (i + 1))[i]}
            if best is None or candidate["accepted_at"] > best["accepted_at"]:
                best = candidate
    return best or {}


def _vix(day: str, path=MARKET_HISTORY) -> float | None:
    rows = [r for r in _csv(path) if r.get("symbol") == ".VIX" and r.get("market_date") <= day]
    return float(rows[-1]["close"]) if rows else None


def context(ticker: str, entry_day: str, bars=None, spy=None, vix=None) -> dict:
    bars, spy = bars or _bars(ticker), spy or _bars("SPY")
    index = next((i for i, row in enumerate(bars) if row["date"] == entry_day.replace("-", "")), None)
    spy_index = next((i for i, row in enumerate(spy) if row["date"] == entry_day.replace("-", "")), None)
    if index is None or index == 0 or spy_index is None:
        raise ValueError("entry or prior-session price is unavailable")
    prior, current = float(bars[index - 1]["close"]), bars[index]
    if spy_index==0:
        raise ValueError('prior SPY session unavailable')
    spy_closes = [float(row["close"]) for row in spy[max(0, spy_index - 20):spy_index]]
    spy_close = spy_closes[-1]
    spy_ma20 = sum(spy_closes) / len(spy_closes) if len(spy_closes) == 20 else None
    prior_day=spy[spy_index-1]['date']
    prior_day=f'{prior_day[:4]}-{prior_day[4:6]}-{prior_day[6:]}'
    vix = _vix(prior_day) if vix is None else vix
    defensive = (vix is not None and vix >= 25) or (spy_ma20 is not None and spy_close < spy_ma20)
    return {"bars": bars, "index": index, "prior_close": prior,
            "gap_pct": (float(current["open"]) / prior - 1) * 100,
            "vix": vix, "spy_close": spy_close, "spy_ma20": spy_ma20, "defensive": defensive}


def _return(entry: float, exit_price: float) -> float:
    return ((exit_price * (1 - COST)) / (entry * (1 + COST)) - 1) * 100


def simulate(ctx: dict, earnings: bool) -> dict:
    bars, start = ctx["bars"], ctx["index"]
    immediate = float(bars[start]["open"])
    confirmed = start + 1 if float(bars[start]["close"]) >= immediate and start + 1 < len(bars) else None
    results = {}
    for horizon in HORIZONS:
        end = start + horizon - 1
        if end >= len(bars):
            continue
        close = float(bars[end]["close"])
        base = _return(immediate, close)
        results[str(horizon)] = {
            "immediate": round(base, 4),
            "gap_filter": round(0.0 if ctx["gap_pct"] >= 3 else base, 4),
            "confirmed": round(_return(float(bars[confirmed]["open"]), close), 4) if confirmed is not None and confirmed <= end else 0.0,
            "risk_scaled": round(base * (0.5 if ctx["defensive"] else 1.0), 4),
            "earnings_confirmed": round(_return(float(bars[confirmed]["open"]), close), 4) if earnings and confirmed is not None and confirmed <= end else (0.0 if earnings else None),
        }
    return results


def harvest(path=PATH, event_fetch=sec_event) -> int:
    added = 0
    with closing(connect(path)) as db:
        for account in ACCOUNTS:
            names = {r["code"]: r.get("name_ko") or r.get("name_eng") or r["code"] for r in _csv(account.path("universe.csv"))}
            for row in _csv(account.path("decisions.csv")):
                if row.get("event") != "buy":
                    continue
                key = (account.key, row["ticker"], row["date"])
                if db.execute("SELECT 1 FROM episodes WHERE account=? AND ticker=? AND entry_day=?", key).fetchone():
                    continue
                now = datetime.now().isoformat(timespec="seconds")
                try:
                    ctx = context(row["ticker"], row["date"])
                    try:
                        filing, sec_error = event_fetch(row["ticker"], row["date"]), None
                    except Exception as exc:
                        filing, sec_error = {}, f"SEC: {exc}"
                    db.execute("""INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (*key[:1], key[1], names.get(row["ticker"], row["ticker"]), key[2], int(row["shares"]), float(row["price_usd"]),
                                ctx["prior_close"], ctx["gap_pct"], ctx["vix"], ctx["spy_close"], ctx["spy_ma20"], int(ctx["defensive"]),
                                filing.get("form"), filing.get("accepted_at"), filing.get("accession"), "{}", "PENDING", sec_error, now, now))
                    added += 1
                except Exception as exc:
                    db.execute("""INSERT INTO episodes(account,ticker,name,entry_day,shares,entry_price,defensive,state,error,observed_at,updated_at)
                                  VALUES(?,?,?,?,?,?,0,'ERROR',?,?,?)""",
                               (key[0], key[1], names.get(key[1], key[1]), key[2], int(row["shares"]), float(row["price_usd"]), str(exc), now, now))
                db.commit()
    return added


def run(path=PATH) -> dict:
    added, updated, failures = harvest(path), 0, 0
    with closing(connect(path)) as db:
        for row in db.execute("SELECT * FROM episodes WHERE state!='COMPLETE'").fetchall():
            try:
                ctx = context(row["ticker"], row["entry_day"], vix=row["vix"])
                filing = {"form": row["sec_form"], "accepted_at": row["sec_accepted_at"], "accession": row["sec_accession"]}
                error = None
                if row["state"] == "ERROR" or str(row["error"] or "").startswith("SEC:"):
                    try:
                        filing = sec_event(row["ticker"], row["entry_day"])
                    except Exception as exc:
                        error = f"SEC: {exc}"
                        failures += 1
                outcomes = simulate(ctx, bool(filing.get("form")))
                state = "COMPLETE" if "20" in outcomes and error is None else "PENDING"
                db.execute("""UPDATE episodes SET prior_close=?,gap_pct=?,vix=?,spy_close=?,spy_ma20=?,defensive=?,
                              sec_form=?,sec_accepted_at=?,sec_accession=?,outcomes=?,state=?,error=?,updated_at=?
                              WHERE account=? AND ticker=? AND entry_day=?""",
                           (ctx["prior_close"], ctx["gap_pct"], ctx["vix"], ctx["spy_close"], ctx["spy_ma20"], int(ctx["defensive"]),
                            filing.get("form"), filing.get("accepted_at"), filing.get("accession"), json.dumps(outcomes), state, error,
                            datetime.now().isoformat(timespec="seconds"), row["account"], row["ticker"], row["entry_day"]))
                updated += 1
            except Exception as exc:
                failures += 1
                db.execute("UPDATE episodes SET state='ERROR',error=?,updated_at=? WHERE account=? AND ticker=? AND entry_day=?",
                           (str(exc), datetime.now().isoformat(timespec="seconds"), row["account"], row["ticker"], row["entry_day"]))
            db.commit()
        total = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    return {"version": VERSION, "added": added, "updated": updated, "failures": failures, "episodes": total}


def dashboard_section(path=PATH) -> str:
    if not Path(path).exists():
        return "<section><h2>미국 진입 관찰실험</h2><p class='muted'>첫 가상매수 체결 후 관찰을 시작합니다.</p></section>"
    db = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    with closing(db):
        rows = db.execute("SELECT * FROM episodes ORDER BY entry_day DESC,account,ticker LIMIT 30").fetchall()
        counts = db.execute("SELECT COUNT(*) total,SUM(state='COMPLETE') complete,SUM(error IS NOT NULL) errors FROM episodes").fetchone()
    body = []
    for row in rows:
        outcomes = json.loads(row["outcomes"] or "{}")
        result = outcomes.get("10", {})
        values = [row["entry_day"], row["account"], f"{row['name']} ({row['ticker']})", f"{row['gap_pct']:+.2f}%" if row["gap_pct"] is not None else "-",
                  f"{row['vix']:.2f}" if row["vix"] is not None else "자료 없음", "방어" if row["defensive"] else "일반",
                  row["sec_form"] or "없음", f"{result['immediate']:+.2f}%" if "immediate" in result else "관찰 중",
                  f"{result['gap_filter']:+.2f}%" if "gap_filter" in result else "관찰 중",
                  f"{result['risk_scaled']:+.2f}%" if "risk_scaled" in result else "관찰 중",
                  "오류" if row["error"] else "완료" if row["state"] == "COMPLETE" else "관찰 중"]
        body.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>")
    heads = ["진입일", "계좌", "종목", "시가 갭", "VIX", "시장", "실적 공시", "10일 기존", "10일 갭 필터", "10일 위험 비중", "상태"]
    table = ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
             if rows else "<p class='muted'>첫 가상매수 체결을 기다리고 있습니다.</p>")
    return ("<section><div class='table-heading'><h2>미국 진입 관찰실험</h2>"
            f"<span class='table-count'>전체 {counts['total']}건 · 완료 {counts['complete'] or 0}건 · 오류 {counts['errors'] or 0}건</span></div>"
            "<p class='muted'>실제 주문은 바꾸지 않습니다. 실적 공시 후 즉시·확인 진입, VIX 25 이상 또는 SPY 20일선 하회 시 절반 비중, +3% 이상 시가 갭 회피를 1·3·5·10·20거래일로 비교합니다.</p>"
            + table + "</section>")


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
