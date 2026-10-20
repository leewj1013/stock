"""Observe Naver market rankings, investor flows, warnings and funding data."""
from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/naver_market_signals.db"
BASE = "https://stock.naver.com/api"
RANKS = {"high52": "high52week", "low52": "low52week", "volume_surge": "upperQuantTop",
         "trading_value": "priceTop", "management": "statusTag", "halt": "tradeStopYn"}
ALERTS = {"attention": "01", "warning": "02", "risk": "03"}
LABELS = {"high52": "52주 최고", "low52": "52주 최저", "volume_surge": "거래량 급증",
          "trading_value": "거래대금 상위", "management": "관리종목", "halt": "거래정지",
          "attention": "투자주의", "warning": "투자경고", "risk": "투자위험",
          "foreign_buy": "외국인 순매수", "foreign_sell": "외국인 순매도",
          "organization_buy": "기관 순매수", "organization_sell": "기관 순매도"}
STOCK_KINDS = tuple(RANKS) + tuple(ALERTS)


def _get(path: str) -> object:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0 stockAlarm market-observation"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _day(value: str) -> str:
    text = str(value or "")[:10]
    return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else text).isoformat()


def connect(path: str = PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS signal_rows(
            kind TEXT NOT NULL,market TEXT NOT NULL,market_day TEXT NOT NULL,ticker TEXT NOT NULL,
            rank INTEGER,observed_at TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(kind,market,market_day,ticker));
        CREATE TABLE IF NOT EXISTS signal_state(
            kind TEXT NOT NULL,market TEXT NOT NULL,last_success_at TEXT,last_day TEXT,last_count INTEGER,
            last_attempt_at TEXT NOT NULL,last_error TEXT,
            PRIMARY KEY(kind,market));
        CREATE TABLE IF NOT EXISTS recommendation_signals(
            pick_day TEXT NOT NULL,ticker TEXT NOT NULL,name TEXT NOT NULL,observed_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(pick_day,ticker));
    """)
    db.commit()
    return db


def _save(db: sqlite3.Connection, kind: str, market: str, rows: list[dict], observed: str, fallback_day: str | None = None) -> None:
    days = {row["market_day"] for row in rows}
    if len(days) > 1:
        raise ValueError(f"Mixed source dates: {kind}/{market}")
    day = next(iter(days), fallback_day or date.today().isoformat())
    if not rows and not fallback_day:
        previous = db.execute("SELECT last_day FROM signal_state WHERE kind=? AND market=?", (kind, market)).fetchone()
        if previous and previous[0]:
            day = previous[0]
    db.execute("DELETE FROM signal_rows WHERE kind=? AND market=? AND market_day=?", (kind, market, day))
    db.executemany("INSERT INTO signal_rows VALUES (?,?,?,?,?,?,?)",
                   [(kind, market, row["market_day"], row["ticker"], row.get("rank"), observed,
                     json.dumps(row["payload"], ensure_ascii=False)) for row in rows])
    db.execute("""INSERT INTO signal_state VALUES (?,?,?,?,?,?,NULL)
        ON CONFLICT(kind,market) DO UPDATE SET last_success_at=excluded.last_success_at,
        last_day=excluded.last_day,last_count=excluded.last_count,last_attempt_at=excluded.last_attempt_at,last_error=NULL""",
        (kind, market, observed, day, len(rows), observed))
    db.commit()


def _error(db: sqlite3.Connection, kind: str, market: str, observed: str, error: Exception) -> None:
    db.execute("""INSERT INTO signal_state VALUES (?,?,NULL,NULL,NULL,?,?)
        ON CONFLICT(kind,market) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,last_error=excluded.last_error""",
        (kind, market, observed, str(error)[:200]))
    db.commit()


def _rank_rows(items: object, day: str) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("Invalid stock ranking response")
    rows = []
    for rank, item in enumerate(items, 1):
        ticker = str(item.get("itemcode") or "")
        if not (ticker.isdigit() and len(ticker) == 6):
            continue
        rows.append({"market_day": day, "ticker": ticker, "rank": rank, "payload": item})
    return rows


def _flow_rows(payload: object, side: str) -> list[dict]:
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, dict) or not isinstance(sections.get(side), list):
        raise ValueError("Invalid investor ranking response")
    rows = []
    for rank, item in enumerate(sections[side], 1):
        ticker = str(item.get("itemcode") or "")
        if ticker.isdigit() and len(ticker) == 6 and item.get("bizdateTo"):
            rows.append({"market_day": _day(item["bizdateTo"]), "ticker": ticker, "rank": rank, "payload": item})
    return rows


def _macro_rows(payload: object, kind: str, market: str) -> list[dict]:
    if kind == "index_flow":
        if not isinstance(payload, list):
            raise ValueError("Invalid index response")
        items = [item for item in payload if item.get("itemCode") == market]
        if not items or not items[0].get("additionalInfo"):
            raise ValueError(f"Missing {market} index flow")
        item, source_day = items[0], _day(items[0]["localTradedAt"])
    else:
        items = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError(f"Empty {kind} response")
        item, source_day = items[0], _day(items[0]["bizdate"])
    return [{"market_day": source_day, "ticker": "", "rank": None, "payload": item}]


def _requests(trading_day: str):
    stock_url = "/domestic/market/stock/default?tradeType=KRX&marketType=ALL&startIdx=0&pageSize=100&orderType="
    for kind, order in RANKS.items():
        yield kind, "ALL", stock_url + order, "rank", trading_day
    for kind, alert in ALERTS.items():
        yield kind, "ALL", stock_url + "marketAlertType&alertType=" + alert, "rank", trading_day
    for market in ("KOSPI", "KOSDAQ"):
        for kind, investor in (("foreign", "FOREIGNER"), ("organization", "ORGANIZATION")):
            path = ("/domestic/market/trend/trendForeignOrg?" + urllib.parse.urlencode(
                {"investorType": investor, "tradeType": "KRX", "marketType": market,
                 "startIdx": 0, "pageSize": 20, "periodType": "DAY"}))
            yield kind, market, path, "flow", None
        yield "program", market, ("/domestic/market/trendProgram?" + urllib.parse.urlencode(
            {"tradeType": "KRX", "krxMarketType": market, "bizdate": date.today().strftime("%Y%m%d"),
             "startIdx": 0, "pageSize": 30, "periodType": "DAY"})), "macro", None
    yield "index_flow", "ALL", ("/securityService/integration/indicators?" + urllib.parse.urlencode(
        {"indicatorCodes": "KOSPI,KOSDAQ", "additionalInfo": "KOSPI_KOSDAQ"})), "index", None
    yield "funds", "ALL", "/domestic/market/trendDeposit?startIdx=0&pageSize=20", "macro", None


def collect(path: str = PATH, trading_day: str | None = None) -> dict:
    if trading_day is None:
        from .app import latest_naver_trading_day
        trading_day = latest_naver_trading_day().isoformat()
    observed = datetime.now().astimezone().isoformat(timespec="seconds")
    counts, errors = {}, []
    with closing(connect(path)) as db:
        for kind, market, url, shape, day in _requests(trading_day):
            try:
                payload = _get(url)
                if shape == "rank":
                    parts = [(kind, market, _rank_rows(payload, day))]
                elif shape == "flow":
                    parts = [(kind + "_buy", market, _flow_rows(payload, "buyRankList")),
                             (kind + "_sell", market, _flow_rows(payload, "sellRankList"))]
                elif shape == "index":
                    parts = [(kind, name, _macro_rows(payload, kind, name)) for name in ("KOSPI", "KOSDAQ")]
                else:
                    parts = [(kind, market, _macro_rows(payload, kind, market))]
                for label, selected_market, rows in parts:
                    _save(db, label, selected_market, rows, observed, day)
                    counts[f"{label}/{selected_market}"] = len(rows)
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                for failed_kind, failed_market in (
                    ((kind + "_buy", market), (kind + "_sell", market)) if shape == "flow" else
                    ((kind, "KOSPI"), (kind, "KOSDAQ")) if shape == "index" else
                    ((kind, market),)
                ):
                    _error(db, failed_kind, failed_market, observed, exc)
                errors.append(f"{kind}/{market}: {exc}")
    if not counts:
        raise RuntimeError("All Naver market signal collections failed: " + "; ".join(errors))
    return {"sources": len(counts), "rows": sum(counts.values()), "errors": errors}


def latest(kind: str, market: str = "ALL", path: str = PATH) -> tuple[dict | None, list[dict]]:
    if not Path(path).exists():
        return None, []
    with closing(sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        try:
            state = db.execute("SELECT * FROM signal_state WHERE kind=? AND market=?", (kind, market)).fetchone()
            if not state or not state["last_day"]:
                return dict(state) if state else None, []
            rows = db.execute("""SELECT * FROM signal_rows WHERE kind=? AND market=? AND market_day=?
                ORDER BY rank,ticker""", (kind, market, state["last_day"])).fetchall()
            return dict(state), [dict(row) for row in rows]
        except sqlite3.DatabaseError:
            return None, []


def _before(source_time: str, decision_time: str) -> bool:
    source, decision = datetime.fromisoformat(source_time), datetime.fromisoformat(decision_time)
    if decision.tzinfo is None:
        decision = decision.replace(tzinfo=source.tzinfo)
    return source <= decision


def harvest(source: str = DB_PATH, path: str = PATH, today: date | None = None) -> int:
    day = (today or date.today()).isoformat()
    selected = query_rows("""SELECT c.ticker,c.name,c.evaluated_at,c.benchmark_symbol FROM candidate_snapshots c
        JOIN strategy_runs r ON r.run_id=c.run_id WHERE r.market_date=? AND c.selected=1
        ORDER BY c.evaluated_at DESC""", (day,), path=source)
    if not selected or not Path(path).exists():
        return 0
    selected = list({row["ticker"]: row for row in reversed(selected)}.values())
    kinds = list(STOCK_KINDS) + ["foreign_buy", "foreign_sell", "organization_buy", "organization_sell"]
    snapshots = {(kind, market): latest(kind, market, path) for kind in kinds
                 for market in (("ALL",) if kind in STOCK_KINDS else ("KOSPI", "KOSDAQ"))}
    snapshots.update({(kind, market): latest(kind, market, path) for kind in ("program", "index_flow")
                      for market in ("KOSPI", "KOSDAQ")})
    snapshots[("funds", "ALL")] = latest("funds", "ALL", path)
    count = 0
    with closing(connect(path)) as db:
        existing = {row[0] for row in db.execute("SELECT ticker FROM recommendation_signals WHERE pick_day=?", (day,))}
        for candidate in selected:
            ticker, evaluated = candidate["ticker"], candidate["evaluated_at"]
            if ticker in existing:
                continue
            context = {}
            for (kind, market), (state, rows) in snapshots.items():
                if not state or not state["last_success_at"] or not _before(state["last_success_at"], evaluated):
                    continue
                if market not in ("ALL", candidate.get("benchmark_symbol")):
                    continue
                matched = next((row for row in rows if row["ticker"] == ticker), None) if kind not in ("program", "index_flow", "funds") else next(iter(rows), None)
                key = kind if market == "ALL" else f"{kind}_{market}"
                context[key] = {"basis_day": state["last_day"], "rank": matched["rank"] if matched else None,
                                "payload": json.loads(matched["payload"]) if matched else None}
            if not context:
                continue
            count += db.execute("INSERT OR IGNORE INTO recommendation_signals VALUES (?,?,?,?,?,?)",
                (day, ticker, candidate["name"] or ticker, evaluated, datetime.now().astimezone().isoformat(timespec="seconds"),
                 json.dumps(context, ensure_ascii=False))).rowcount
        db.commit()
    return count


def _table(heads: list[str], rows: list[list[object]]) -> str:
    return ("<div class='table-scroll'><table><thead><tr>" + "".join(f"<th>{escape(head)}</th>" for head in heads)
            + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row)
              + "</tr>" for row in rows) + "</tbody></table></div>")


def dashboard_section(path: str = PATH) -> str:
    if not Path(path).exists():
        return "<section><h2>네이버 시장 보조지표</h2><p class='muted'>수집 전입니다.</p></section>"
    body = ["<section><h2>네이버 시장 보조지표</h2><p class='muted'>조회 당시 기록 · 추천 점수와 주문에는 적용하지 않은 관찰 자료입니다.</p>"]
    for kind in ("high52", "low52", "volume_surge", "trading_value"):
        state, rows = latest(kind, path=path)
        when = f"{state['last_day']} · 확인 {state['last_success_at']}" if state and state["last_day"] else "수집 전"
        visible = []
        for row in rows[:10]:
            item = json.loads(row["payload"])
            visible.append([row["rank"], item.get("itemname", ""), str(item.get("prevChangeRate") or "") + "%",
                            f"{int(float(item.get('tradeAmount') or 0)) / 1e8:,.0f}억",
                            str(item.get("quantDiffRate") or "") + "%"])
        body.append(f"<h3>{LABELS[kind]} · {escape(when)}</h3>" +
                    (_table(["순위", "종목", "등락률", "거래대금", "거래량 증감"], visible) if visible else "<p class='muted'>해당 종목 없음 또는 수집 전</p>"))
    warning_rows = []
    try:
        from .positions_check import active_position_tickers
        held = active_position_tickers()
    except (OSError, ValueError, sqlite3.Error):
        held = set()
    for kind in ("management", "halt", "attention", "warning", "risk"):
        state, rows = latest(kind, path=path)
        flagged = [row for row in rows if row["ticker"] in held]
        warning_rows.append([LABELS[kind], state["last_day"] if state else "-", len(rows),
                             ", ".join(json.loads(row["payload"]).get("itemname", row["ticker"]) for row in flagged) or "-"])
    body.append("<h3>관리·거래정지·경보 (각 목록 최대 100종목)</h3>" +
                _table(["구분", "기준일", "수집 종목", "현재 보유종목"], warning_rows))
    flow_rows = []
    for market in ("KOSPI", "KOSDAQ"):
        _, index = latest("index_flow", market, path)
        program_state, program = latest("program", market, path)
        if index:
            info = json.loads(index[0]["payload"]).get("additionalInfo") or {}
            prog = json.loads(program[0]["payload"]) if program else {}
            flow_rows.append([index[0]["market_day"], market,
                              info.get("foreignerNetBuyAmount", "-"), info.get("organizationNetBuyAmount", "-"),
                              info.get("individualNetBuyAmount", "-"),
                              f"{int(prog['totalDiffPureBuyAmt']) / 1e8:+,.0f}" if prog.get("totalDiffPureBuyAmt") else "-",
                              program_state["last_day"] if program_state else "-"])
    body.append("<h3>시장 수급·프로그램 순매수 (억원)</h3>" +
                (_table(["수급 기준일", "시장", "외국인", "기관", "개인", "프로그램", "프로그램 기준일"], flow_rows) if flow_rows else "<p class='muted'>수집 전</p>"))
    investor_rows = []
    for market in ("KOSPI", "KOSDAQ"):
        for kind in ("foreign_buy", "foreign_sell", "organization_buy", "organization_sell"):
            state, rows = latest(kind, market, path)
            names = ", ".join(json.loads(row["payload"]).get("itemname", row["ticker"]) for row in rows[:5])
            investor_rows.append([state["last_day"] if state else "-", market, LABELS[kind], names or "-"])
    body.append("<h3>외국인·기관 순매매 상위</h3>" + _table(["기준일", "시장", "구분", "상위 5종목"], investor_rows))
    state, funds = latest("funds", path=path)
    if funds:
        item = json.loads(funds[0]["payload"])
        body.append("<h3>증시자금동향 · 기준 " + escape(state["last_day"]) + " (억원)</h3>" +
                    _table(["고객예탁금", "전일 증감", "신용잔고", "전일 증감"],
                           [[item.get("customerDeposit", "-"), item.get("customerDepositDiff", "-"),
                             item.get("creditLoan", "-"), item.get("creditLoanDiff", "-")]]))
    else:
        body.append("<h3>증시자금동향</h3><p class='muted'>수집 전</p>")
    with closing(sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)) as db:
        issues = db.execute("SELECT kind,market,last_error FROM signal_state WHERE last_error IS NOT NULL").fetchall()
    if issues:
        body.append("<p class='muted'>수집 지연: " + escape(", ".join(f"{kind}/{market}" for kind, market, _ in issues)) + "</p>")
    return "".join(body) + "</section>"


def recommendation_section(path: str = PATH, outcomes_path: str = DB_PATH) -> str:
    rows = query_rows("SELECT * FROM recommendation_signals ORDER BY observed_at DESC,ticker LIMIT 30", path=path)
    if not rows:
        return "<section><h2>추천 시점 시장 보조지표</h2><p class='muted'>새 추천부터 기록됩니다.</p></section>"
    outcomes = query_rows("""SELECT pick_date,ticker,return_10d_pct FROM recommendation_outcomes
        WHERE quality_status='valid' ORDER BY updated_at DESC""", path=outcomes_path)
    returns = {(row["pick_date"], row["ticker"]): row["return_10d_pct"] for row in reversed(outcomes)}
    display = []
    for row in rows:
        info = json.loads(row["payload"])
        warnings = [LABELS[kind] for kind in ("management", "halt", "attention", "warning", "risk")
                    if info.get(kind, {}).get("rank")]
        value = returns.get((row["pick_day"], row["ticker"]))
        display.append([row["pick_day"], row["name"], info.get("high52", {}).get("rank") or "-",
                        info.get("low52", {}).get("rank") or "-", info.get("volume_surge", {}).get("rank") or "-",
                        info.get("foreign_buy_KOSPI", info.get("foreign_buy_KOSDAQ", {})).get("rank") or "-",
                        info.get("organization_buy_KOSPI", info.get("organization_buy_KOSDAQ", {})).get("rank") or "-",
                        ", ".join(warnings) or "-", f"{value:+.2f}%" if value is not None else "관찰 중"])
    return ("<section><h2>추천 시점 시장 보조지표</h2><p class='muted'>추천 이전에 확인한 순위만 기록합니다. 빈 순위는 상위 목록에 없거나 해당 자료가 없음을 뜻합니다.</p>"
            + _table(["추천일", "종목", "52주 최고", "52주 최저", "거래량 급증", "외국인 순매수", "기관 순매수", "경보", "10일 수익률"], display)
            + "</section>")


if __name__ == "__main__":
    import sys
    print({"recommendation_signals": harvest()} if len(sys.argv) > 1 and sys.argv[1] == "harvest" else collect())
