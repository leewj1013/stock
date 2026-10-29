"""Concentration diagnostics and prospective signal expiry; no live trading changes."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/research_extensions_v1.db"
VERSION = "research-extensions-v1"
RULES = {"fee_bps_each_side": 15, "expiry_session_offset": 1, "expiry_time": "15:30:00",
         "diagnostic_exclusions": [1, 3], "proposed_spread_max_pct": 0.3,
         "proposed_event_sale_fraction": 0.5, "proposed_event_decision_time": "14:50:00"}


def trade_outcomes(buys, sales):
    """Only fully closed, non-overlapping buys have an attributable net outcome."""
    by_ticker = {}
    for buy in sorted(buys, key=lambda r: (r["created_at"], r["trade_id"])):
        by_ticker.setdefault(buy["ticker"], []).append(buy)
    result = []
    fee = RULES["fee_bps_each_side"] / 10000
    for ticker, entries in by_ticker.items():
        ticker_sales = [s for s in sales if s["ticker"] == ticker]
        for i, buy in enumerate(entries):
            end = entries[i+1]["created_at"] if i+1 < len(entries) else "9999"
            matched = [s for s in ticker_sales if buy["created_at"] <= s["created_at"] < end]
            sold = sum(int(s["quantity"]) for s in matched)
            quantity = int(buy["quantity"])
            if quantity <= 0 or not math.isfinite(float(buy["price"])) or float(buy["price"]) <= 0 or any(int(s["quantity"]) <= 0 or not math.isfinite(float(s["price"])) or float(s["price"]) <= 0 for s in matched):
                raise ValueError("Invalid trade prices or quantities")
            state = "ERROR" if sold > quantity or (sold < quantity and end != "9999") else "COMPLETE" if sold == quantity else "OPEN"
            net = (sum(float(s["price"]) * int(s["quantity"]) for s in matched) * (1-fee)
                   - float(buy["price"]) * quantity * (1+fee)) if state == "COMPLETE" else None
            result.append({"trade_id": buy["trade_id"], "ticker": ticker, "name": buy.get("name") or ticker,
                           "bought_at": buy["created_at"], "state": state, "net_profit": net})
    return sorted(result, key=lambda r: (r["bought_at"], r["trade_id"]), reverse=True)


def concentration(outcomes):
    completed = [r for r in outcomes if r["state"] == "COMPLETE"]
    winners = sorted((r["net_profit"] for r in completed if r["net_profit"] > 0), reverse=True)
    total, positive = sum(r["net_profit"] for r in completed), sum(winners)
    rows = [["전체 종료 거래", str(len(completed)), "0", f"{total:+,.0f}원", "—"]]
    shares = []
    for n in RULES["diagnostic_exclusions"]:
        removed = sum(winners[:n])
        count = min(n, len(winners))
        rows.append([f"수익 상위 {n}건 기여 제외", str(len(completed)-count), str(count), f"{total-removed:+,.0f}원", f"{-removed:+,.0f}원"])
        shares.append([f"상위 {n}건", f"{removed/positive*100:.1f}%" if positive else "양의 손익 없음"])
    return {"complete": len(completed), "open": sum(r["state"] == "OPEN" for r in outcomes),
            "errors": sum(r["state"] == "ERROR" for r in outcomes), "rows": rows, "shares": shares}


def expiry_at(signal_at, bought_at, sessions):
    """Session dates must be saved index sessions, never inferred weekdays."""
    if not signal_at:
        return None
    days = sorted(set(sessions))
    signal_day, buy_day = signal_at[:10], bought_at[:10]
    if signal_day not in days or buy_day not in days:
        return None
    index = days.index(signal_day) + RULES["expiry_session_offset"]
    if index >= len(days):
        return None
    return days[index] + "T" + RULES["expiry_time"]


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(key TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS expiry_cases(trade_id INTEGER PRIMARY KEY,ticker TEXT NOT NULL,
            bought_at TEXT NOT NULL,signal_at TEXT,expires_at TEXT,state TEXT NOT NULL,
            baseline_profit REAL,candidate_profit REAL,updated_at TEXT NOT NULL);
    """)
    for key, value in (("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True)),
                       ("activated_at", datetime.now().isoformat(timespec="seconds"))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Research extension rules changed; use a new versioned database")
    db.commit()
    return db


def run(source=DB_PATH, path=PATH, sessions=None):
    if sessions is None:
        from .dashboard_v2 import _index_cache
        sessions = [f"{day[:4]}-{day[4:6]}-{day[6:]}" for day, _ in _index_cache("KOSPI")]
    with closing(connect(path)) as db, closing(sqlite3.connect(Path(source).resolve().as_uri()+"?mode=ro", uri=True)) as src:
        src.row_factory = sqlite3.Row
        buys = [dict(r) for r in src.execute("SELECT * FROM virtual_trades ORDER BY created_at,trade_id")]
        sales = [dict(r) for r in src.execute("SELECT * FROM virtual_sales ORDER BY created_at,sale_id")]
        outcomes = trade_outcomes(buys, sales)
        now = datetime.now().isoformat(timespec="seconds")
        diagnostic = concentration(outcomes)
        diagnostic["source_as_of"] = max((s["created_at"] for s in sales), default="기록 없음")
        db.execute("INSERT OR REPLACE INTO snapshots VALUES ('concentration',?,?)", (json.dumps(diagnostic, ensure_ascii=False),now))
        start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        for trade in buys:
            if trade["created_at"] < start:
                continue
            signal = src.execute("""SELECT max(evaluated_at) FROM candidate_snapshots
                WHERE ticker=? AND selected=1 AND evaluated_at<=?""", (trade["ticker"],trade["created_at"])).fetchone()[0]
            db.execute("INSERT OR IGNORE INTO expiry_cases(trade_id,ticker,bought_at,signal_at,state,updated_at) VALUES (?,?,?,?,'DATA_REQUIRED',?)",
                       (trade["trade_id"],trade["ticker"],trade["created_at"],signal,now))
        by_id = {r["trade_id"]:r for r in outcomes}
        for case in db.execute("SELECT * FROM expiry_cases WHERE state!='COMPLETE'").fetchall():
            outcome = by_id.get(case["trade_id"])
            expires = expiry_at(case["signal_at"],case["bought_at"],sessions)
            state = "ERROR" if not outcome or outcome["state"] == "ERROR" else "DATA_REQUIRED" if not expires else outcome["state"]
            baseline = outcome["net_profit"] if state == "COMPLETE" else None
            candidate = (0 if case["bought_at"] > expires else baseline) if state == "COMPLETE" else None
            db.execute("UPDATE expiry_cases SET expires_at=?,state=?,baseline_profit=?,candidate_profit=?,updated_at=? WHERE trade_id=?",
                       (expires,state,baseline,candidate,now,case["trade_id"]))
        db.commit()
        count = db.execute("SELECT count(*) FROM expiry_cases").fetchone()[0]
        errors = db.execute("SELECT count(*) FROM expiry_cases WHERE state='ERROR'").fetchone()[0]
    return {"version":VERSION,"diagnostic_complete":diagnostic["complete"],"expiry_cases":count,
            "failures":diagnostic["errors"]+errors}


def dashboard_data(path=PATH):
    from .research_data_collect import dashboard_data as source_data
    evidence=source_data()
    gate_path=Path('reports/research_priority/status.json')
    try:
        gate=json.loads(gate_path.read_text(encoding='utf-8')) if gate_path.exists() else None
    except (OSError,ValueError):
        gate=None
    gate_summary=(f"확인 {gate['checked_at']} · 업종 누락 {gate['industry']['excluded_pairs']}건 제외 · "
                  f"공매도 {gate['short_balance']['availability_checked']}행 시점 검사 · "
                  f"호가 1주 비용 점검 {gate['quotes']['adjacent_one_share_scenario_pairs']}쌍. 체결 수익률 아님") if gate else "입력 사용 조건 점검 대기"
    try:
        followup=json.loads(Path('reports/four_remaining/status.json').read_text(encoding='utf-8'))
        progress=followup.get('quote_exit_progress',{})
        followup_summary=(f"동시호가 전 호가 청산 별도 등록 {followup['quote_exit_protocol']['activated_at']} · "
                          f"신규 매수 {followup['prospective_cohorts'][0]['new_buys']}건 · "
                          f"호가 관찰 등록 {progress.get('registered',0)}·대기 {progress.get('waiting',0)}·"
                          f"자료 제외 {progress.get('excluded_missing_data',0)}·완료 {followup['quote_exit_counts'].get('SCENARIO_COMPLETE',0)}건 · "
                          f"과거 탐색 비교 {followup['historical_replay']['included']}건은 별도. 철회 표본 검증은 보류")
    except (OSError,ValueError,KeyError,TypeError):
        followup_summary='추가 검증 갱신 대기'
    try:
        withdrawal=json.loads(Path('reports/reverse_withdrawal/result.json').read_text(encoding='utf-8'))
        withdrawal_summary=(f"일반 철회 감지 실자료 {withdrawal['generic_withdrawal_verified']}사례 검증 · "
                            f"실적 예고 철회 {withdrawal['earnings_withdrawal_verified']}건(표본 확보 보류). 일반 감지 검증과 실적 일정 검증을 분리")
    except (OSError,ValueError,KeyError,TypeError):
        withdrawal_summary='철회 문서 역추적 검증 대기'
    try:
        trace=json.loads(Path('reports/earnings_event_trace/result.json').read_text(encoding='utf-8'))
        trace_summary=(f"실적발표 IR 일정 미정·재확정 {trace['earnings_ir_uncertainty_cases']}사례 · "
                       f"일반 IR 취소 {trace['ir_cancellation_documents']}문서 · "
                       f"실적 예고 정식 철회 {trace['formal_earnings_notice_withdrawals']}건. 서로 대체 집계하지 않음")
    except (OSError,ValueError,KeyError,TypeError):
        trace_summary='실적 발표 후속 공시 대조 대기'
    scanned=sum(r['n'] for r in evidence['statuses'] if r['kind']=='announcements' and r['state']=='SCANNED')
    krx_summary=" · ".join(f"{ {'industry':'업종 분류','short_balance':'공매도 잔고'}.get(r['kind'],r['kind'])} {r['snapshots']}개 스냅샷" for r in evidence.get('krx',[])) or "수집 표본 대기"
    meta = query_rows("SELECT value FROM metadata WHERE key='activated_at'",path=path)
    start = meta[0]["value"] if meta else "수집 준비 중"
    saved = query_rows("SELECT * FROM snapshots WHERE key='concentration'",path=path)
    diagnostic = json.loads(saved[0]["payload"]) if saved else {"complete":0,"open":0,"errors":0,"rows":[],"shares":[],"source_as_of":"기록 없음"}
    cases = query_rows("SELECT * FROM expiry_cases ORDER BY bought_at DESC,trade_id DESC",path=path)
    complete = [r for r in cases if r["state"] == "COMPLETE"]
    expired = [r for r in complete if r["bought_at"] > r["expires_at"]]
    diff = sum(r["candidate_profit"]-r["baseline_profit"] for r in complete)
    plans = [
        ["① 수익 집중도", "과거 진단 갱신" if saved else "진단 대기", "수익 상위 1·3건의 기여를 제외한 순손익과 전체 양의 손익 중 비중 비교. 사후 진단이며 매매 규칙이 아님"],
        ["② 추천 신호 유효기간", "전진 관찰 등록", "최신 추천 다음 거래일 15:30 이후의 지연 매수는 보류·현금 유지. 재추천은 새 시각으로 갱신하며 매도·재진입 실험과 구분"],
        ["③ 호가 차이 매수 보류", f"호가 수집 {evidence['quotes']['n']}건 · KRX·NXT 통합 호가 확인" if evidence['quotes']['n'] else "수집 연결 · 호가 표본 대기", "호가 중간값 대비 매수·매도 호가 차이 0.3% 초과 시 보류, 같은 장에서 축소 확인 시 매수하는 안. 0.3%는 가설값"],
        ["④ 실적 발표 전 보유 축소", f"예고 원문 {evidence['announcements']['n']}건 · 일정 검증 중" if evidence['announcements']['n'] else "수집 연결 · 예고 공시 대기", "사전에 알려진 발표 일정의 직전 거래일 14:50 판단 후 다음 관측 가격에 잔량 50% 축소 vs 계속 보유"],
    ]
    return {"start":start,"plans":plans,"diagnostic":diagnostic,"diagnostic_at":saved[0]["updated_at"] if saved else "미실행",
            "expiry_rows":[["추천 유효기간",str(len(cases)),str(len(complete)),str(len(expired)),
                            f"{diff:+,.0f}원" if complete else "관찰 중",str(sum(r["state"] == "DATA_REQUIRED" for r in cases)),
                            str(sum(r["state"] == "ERROR" for r in cases))]],
            "requirements":[["호가 자료", f"원문 {evidence['quotes']['n']}건, 수집 시점 시각 정상 {evidence['quotes'].get('fresh',0)}건. KRX·NXT 통합 호가로 확인. 개별 거래소·체결 보장은 없으므로 체결 성과 계산 보류"],
                            ["발표 예정 자료", f"대상 {scanned}종목 검색·예고 원문 {evidence['announcements']['n']}건. 최초 확인 시각·정정 원문 보존, 과거 예정일은 성과 제외. 일정 검증 전 성과 계산 보류"],
                            ["재무 공시 연결", f"접수번호·원문 해시 연결 {evidence['statements']['n']}버전/{evidence['statements']['tickers']}종목. 기존 재무 수치는 덮어쓰지 않으며 과거 원본 전체 복원은 미확인"],
                            ["KRX 과거 자료", krx_summary+". 별도 원문 보존이며 과거 시점 정보가 정정되지 않았는지 검증 전 기존 연구 입력을 교체하지 않음"],
                            ["입력 사용 조건",gate_summary],["추가 검증",followup_summary],
                            ["철회 감지 검증",withdrawal_summary],["실적 일정 변경 검증",trace_summary]],"source_evidence":evidence}


def dashboard_section(path=PATH):
    data = dashboard_data(path)

    def table(heads, rows):
        return "<table><thead><tr>"+"".join(f"<th>{escape(h)}</th>" for h in heads)+"</tr></thead><tbody>"+"".join(
            "<tr>"+"".join(f"<td>{escape(str(v))}</td>" for v in row)+"</tr>" for row in rows)+"</tbody></table>"

    d = data["diagnostic"]
    return ("<section><h2>추가 연구 ①–④ · 수익 집중·신호 수명·체결 비용·발표 전 위험</h2>"
            + table(["연구","상태","비교 규칙"],data["plans"])
            + f"<p class='muted'>수익 집중도: 국내 적극투자형 과거 종료 {d['complete']}건 · 보유 {d['open']}건 · 연결 오류 {d['errors']}건 · 원본 최근 매도 {escape(d['source_as_of'])}</p>"
            + table(["진단","잔여 종료 거래","제외 수익 거래","순손익","전체 대비 변화"],d["rows"])
            + table(["수익 거래","전체 양의 손익 중 비중"],d["shares"])
            + f"<p class='muted'>추천 유효기간 관찰 시작 {escape(data['start'])}</p>"
            + table(["연구","등록","완료","만료 매수","기존 대비 순손익 차이","자료 부족","오류"],data["expiry_rows"])
            + table(["필요 자료","자료 대기 사유"],data["requirements"])
            + "<p class='muted'>왕복 비용 0.3%를 표준 적용해 부분 익절과 잔량 매도 손익을 합산합니다. "
            "집중도는 과거 거래의 사후 손익 기여 진단이며 재투자 경로·계좌 수익률·최대낙폭을 재현하지 않습니다. "
            "유효기간 비교는 등록 이후 실제 매수만 대상으로 하며, 보류금은 기존 최종 매도까지 무이자 현금으로 둡니다. "
            "저장 KOSPI 거래일 또는 추천 연결이 없으면 자료 부족으로 남깁니다. 완료된 같은 거래만 비교하고 자금을 다른 종목에 재투자하지 않습니다. "
            "③·④는 자료 수집을 연결했으며 호가는 통합 범위를 확인했으며 체결 모형·발표 일정 검증 전 성과 계산은 보류합니다. 자동 주문은 변경하지 않습니다.</p></section>")


if __name__ == "__main__":
    print(json.dumps(run(),ensure_ascii=False))
