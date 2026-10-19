"""Account-constraint, re-entry and rotation observations with dated context."""
from __future__ import annotations

import json
import math
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from statistics import mean

from .buy_observer import PATH, VERSION, RULES, connect, allocation_plan
from .data_store import DB_PATH, query_rows

LABELS = {"executed": "매수 체결", "no_candidates": "추천 없음", "auto_disabled": "자동매수 꺼짐",
          "cash_empty": "현금 부족", "risk_halt": "위험관리 중단", "invalid_price": "가격 데이터 이상",
          "target_already_met": "기존 보유가 목표 비중 충족", "portfolio_cap": "투자 한도 소진",
          "below_one_share": "1주 매수금액 미달", "minimum_fill": "최소 체결비율 미달",
          "allocation_zero": "배분 비중 0", "correlation_limit": "상관관계 제한", "sector_limit": "업종 제한",
          "unconfirmed": "확정 사유 확인 필요"}


def decisions(payload):
    executed = {r["ticker"] for r in payload.get("executions", [])}
    planned = {r["ticker"]: r for r in payload.get("budget_plan", [])}
    if not payload["picks"]:
        return [{"ticker": "", "name": "", "reason": "no_candidates"}]
    results = []
    for index, pick in enumerate(payload["picks"]):
        row = planned.get(pick["ticker"], {})
        reason = ("executed" if pick["ticker"] in executed else
                  "auto_disabled" if not payload["auto_enabled"] else
                  "cash_empty" if payload.get("state", {}).get("cash") == 0 else
                  "risk_halt" if payload.get("risk_allowed") is False else row.get("reason", "unconfirmed"))
        if reason == "eligible":
            reason = "unconfirmed"  # Model eligibility is not evidence that an order executed.
        if reason == "allocation_zero":
            raw = payload.get("raw_allocations", [])
            corr = payload.get("correlated_allocations", [])
            if index < len(corr) and index < len(raw) and raw[index] > 0:
                sector = payload.get("sector_allocations", [])
                sector_index = len(sector) - len(payload["picks"]) + index
                reason = ("correlation_limit" if corr[index] == 0 else "sector_limit"
                          if 0 <= sector_index < len(sector) and sector[sector_index] == 0 else reason)
        results.append({**pick, **row, "reason": reason})
    return results


def context(payload, ticker):
    return {"regime": payload.get("regime_used", "unknown"),
            "regime_basis": payload.get("regime_basis", "unavailable"),
            "sector": payload.get("sectors", {}).get(ticker, "미분류"),
            "sector_as_of": payload.get("sector_as_of"), "observed_at": payload["observed_at"]}


def harvest(path=PATH, profiles=None):
    if profiles is None:
        from .trading_profiles import PROFILES
        profiles = {k: v["db_path"] for k, v in PROFILES.items() if not v.get("comparison_only")}
    added = 0
    with closing(connect(path)) as db:
        start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        def insert(key, kind, profile, ticker, day, payload):
            nonlocal added
            added += db.execute("INSERT OR IGNORE INTO cases(case_id,kind,profile,ticker,signal_day,payload) VALUES (?,?,?,?,?,?)",
                                (key, kind, profile, ticker, day, json.dumps(payload, ensure_ascii=False))).rowcount
        for attempt in db.execute("SELECT * FROM buy_attempts ORDER BY observed_at,attempt_id").fetchall():
            p = json.loads(attempt["payload"])
            if attempt["error"] or p["profile"] not in profiles or not p.get("candidates") or p.get("risk_allowed") is not True:
                continue
            state, day = p["state"], p["observed_at"][:10]
            equity = state["cash"] + sum(h["cost"] for h in state.get("holdings", []))
            if equity <= 0:
                continue
            baseline = {r["ticker"]: r for r in p["budget_plan"]}
            relaxed = {r["ticker"]: r for r in allocation_plan(p["candidates"], state, p["max_position"], RULES["relaxed_min_fill"])}
            cap = {r["ticker"]: r for r in allocation_plan(p["candidates"], state, p["max_position"], p["min_fill"], RULES["cap_increment_pct"])}
            actual = {r["ticker"]: r["quantity"] for r in p.get("executions", [])}
            # A manual/concurrent account change can invalidate the observed budget reconstruction.
            if any(actual.get(t, 0) != r["quantity"] for t, r in baseline.items()):
                continue
            for row in decisions(p):
                ticker = row["ticker"]
                if row["reason"] not in ("minimum_fill", "portfolio_cap", "below_one_share"):
                    continue
                budgets = {"baseline": baseline[ticker]["cost"], "relaxed_min_fill": relaxed[ticker]["cost"], "higher_cap": cap[ticker]["cost"]}
                if max(budgets.values()) == 0:
                    continue
                insert(f"budget:{p['profile']}:{ticker}:{day}", "budget", p["profile"], ticker, day,
                       {"budgets": budgets, "equity": equity, "context": context(p, ticker), "reason": row["reason"], "attempt_id": attempt["attempt_id"]})
            holdings = state.get("holdings", [])
            held = {h["ticker"] for h in holdings}
            new = [r for r in p["candidates"] if r["ticker"] not in held and float(r.get("allocation_pct") or 0) > 0]
            known = [(h, p.get("evidence", {}).get(h["ticker"], {})) for h in holdings]
            known = [(h, e) for h, e in known if e.get("profile_score_at_observation") is not None and e.get("close", 0) > 0 and e.get("evaluated_at", "")[:10] == day]
            if not new or not known:
                continue
            best = max(new, key=lambda r: r["score"])
            weak, evidence = min(known, key=lambda pair: pair[1]["profile_score_at_observation"])
            quantity = int(weak["quantity"] * RULES["rotation_fraction"])
            if quantity < 1 or best["score"] < evidence["profile_score_at_observation"] + RULES["rotation_score_gap"]:
                continue
            insert(f"rotation:{p['profile']}:{day}", "rotation", p["profile"], best["ticker"], day,
                   {"old_ticker": weak["ticker"], "quantity": quantity, "old_score": evidence["profile_score_at_observation"],
                    "new_score": best["score"], "context": context(p, best["ticker"]), "attempt_id": attempt["attempt_id"]})
        for profile, source in profiles.items():
            for sale in query_rows("SELECT * FROM virtual_sales WHERE created_at>=? AND sale_type='full' ORDER BY created_at,sale_id", (start,), path=source):
                # Cash after a genuine full exit is the control; never infer a historical sale.
                prior = db.execute("SELECT payload FROM buy_attempts WHERE profile=? AND observed_at<=? ORDER BY observed_at DESC LIMIT 1", (profile, sale["created_at"])).fetchone()
                p = json.loads(prior[0]) if prior else {"observed_at": sale["created_at"]}
                insert(f"reentry:{profile}:{sale['sale_id']}", "reentry", profile, sale["ticker"], sale["created_at"][:10],
                       {"sale": sale, "context": context(p, sale["ticker"])})
        db.commit()
    return added


def validate(bars):
    days = [str(r[0]) for r in bars]
    if len(days) != len(set(days)) or days != sorted(days):
        raise ValueError("Duplicate or unsorted sessions")
    for r in bars:
        o, h, l, c = map(float, r[1:5])
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) or h < max(o, c) or l > min(o, c):
            raise ValueError("Invalid OHLC")


def budget_outcomes(payload, bars):
    validate(bars)
    if not bars:
        return {}
    opening, fee = float(bars[0][1]), RULES["fee_bps_each_side"] / 10000
    quantities = {key: int(budget // (opening * (1 + fee))) for key, budget in payload["budgets"].items()}
    results = {}
    for h in RULES["horizons"]:
        if len(bars) < h:
            continue
        close = float(bars[h - 1][4])
        returns = {key: qty * (close * (1 - fee) - opening * (1 + fee)) / payload["equity"] * 100 for key, qty in quantities.items()}
        results[str(h)] = {"returns_pct": returns, "difference_pct": {k: v - returns["baseline"] for k, v in returns.items() if k != "baseline"},
                           "quantities": quantities, "as_of": str(bars[h - 1][0]), "unit": "single_position_account_contribution"}
        if not any(quantities.values()):
            results[str(h)]["skipped"] = "all_variants_unfilled"
    return results


def rotation_outcomes(payload, new, old):
    validate(new)
    validate(old)
    if [r[0] for r in new] != [r[0] for r in old]:
        raise ValueError("Rotation sessions do not align")
    if not new:
        return {}
    fee = RULES["fee_bps_each_side"] / 10000
    original = payload["quantity"] * float(old[0][1])
    proceeds = original * (1 - fee)
    price = float(new[0][1])
    qty = int(proceeds // (price * (1 + fee)))
    if qty == 0:
        return {str(h): {"difference_pct": {"rotate": 0.0}, "skipped": "below_one_share", "as_of": str(new[h-1][0]), "unit": "equal_capital_slice"} for h in RULES["horizons"] if len(new) >= h}
    cash = proceeds - qty * price * (1 + fee)
    results = {}
    for h in RULES["horizons"]:
        if len(new) >= h:
            keep = payload["quantity"] * float(old[h-1][4]) * (1 - fee)
            rotate = cash + qty * float(new[h-1][4]) * (1 - fee)
            results[str(h)] = {"returns_pct": {"keep": (keep / original - 1) * 100, "rotate": (rotate / original - 1) * 100},
                "difference_pct": {"rotate": (rotate - keep) / original * 100}, "as_of": str(new[h-1][0]), "unit": "equal_capital_slice"}
    return results


def reentry_outcomes(payload, history, signal_day):
    validate(history)
    future = [i for i, row in enumerate(history) if str(row[0]) > signal_day.replace("-", "")]
    fee = RULES["fee_bps_each_side"] / 10000
    capital = float(payload["sale"]["proceeds"])
    accounts = {k: {"cash": capital, "qty": 0, "pending": False, "done": False, "price": None} for k in ("wait_three", "trend_reentry")}
    results = {}
    for offset, cursor in enumerate(future[:10]):
        row = history[cursor]
        opening, close = float(row[1]), float(row[4])
        for key, state in accounts.items():
            if state.get("exit_pending"):
                state["cash"] += state["qty"] * opening * (1 - fee)
                state["qty"] = 0
                state["exit_pending"] = False
            if not state["done"] and (state["pending"] or (key == "wait_three" and offset == RULES["reentry_wait_sessions"])):
                qty = int(state["cash"] // (opening * (1 + fee)))
                state.update(qty=qty, cash=state["cash"] - qty * opening * (1 + fee), done=True, pending=False, price=opening, entry_day=str(row[0]))
            if state["qty"] and close <= state["price"] * (1 - RULES["reentry_stop_pct"] / 100):
                state["exit_pending"] = True
        trend = accounts["trend_reentry"]
        ma, confirm = RULES["reentry_ma_sessions"], RULES["reentry_confirm_sessions"]
        if offset >= confirm - 1 and cursor >= ma + confirm - 2 and not trend["done"]:
            trend["pending"] = all(float(history[j][4]) > mean(float(r[4]) for r in history[j-ma+1:j+1]) for j in range(cursor-confirm+1, cursor+1))
        h = offset + 1
        if h in RULES["horizons"]:
            values = {k: (s["cash"] + s["qty"] * close * (1 - fee) - capital) / capital * 100 for k, s in accounts.items()}
            results[str(h)] = {"returns_pct": {"stay_cash": 0, **values}, "difference_pct": values,
                               "entry_days": {k: s.get("entry_day") for k, s in accounts.items()}, "as_of": str(row[0]), "unit": "sale_proceeds"}
    return results


def run(path=PATH, profiles=None, today=None):
    from .app import naver_rows
    added = harvest(path, profiles)
    cutoff = (today or date.today()) - timedelta(days=1)
    failures, updated = 0, 0
    with closing(connect(path)) as db:
        for case in db.execute("SELECT * FROM cases WHERE complete=0 ORDER BY signal_day").fetchall():
            signal = date.fromisoformat(case["signal_day"])
            if signal >= cutoff:
                continue
            p = json.loads(case["payload"])
            try:
                history = naver_rows(case["ticker"], signal - timedelta(days=90), cutoff)
                history = sorted((r for r in history if str(r[0]) <= cutoff.strftime('%Y%m%d')), key=lambda r: str(r[0]))
                future = [r for r in history if str(r[0]) > signal.strftime('%Y%m%d')][:10]
                if case["kind"] == "budget":
                    outcome = budget_outcomes(p, future)
                elif case["kind"] == "rotation":
                    old = naver_rows(p["old_ticker"], signal, cutoff)
                    old = sorted((r for r in old if signal.strftime('%Y%m%d') < str(r[0]) <= cutoff.strftime('%Y%m%d')), key=lambda r: str(r[0]))[:10]
                    outcome = rotation_outcomes(p, future, old)
                else:
                    outcome = reentry_outcomes(p, history, case["signal_day"])
                db.execute("UPDATE cases SET outcomes=?,complete=?,error=NULL,updated_at=? WHERE case_id=?", (json.dumps(outcome), int("10" in outcome), datetime.now().isoformat(timespec="seconds"), case["case_id"]))
                updated += 1
            except Exception as error:
                failures += 1
                db.execute("UPDATE cases SET error=?,updated_at=? WHERE case_id=?", (str(error), datetime.now().isoformat(timespec="seconds"), case["case_id"]))
            # Release the write lock before the next network request so live
            # buy auditing can append concurrently with outcome collection.
            db.commit()
        db.commit()
    return {"added": added, "updated": updated, "failures": failures}


def dashboard_section(path=PATH):
    profile_names = {"aggressive": "적극투자형", "neutral": "위험중립형", "exp_control": "비교 기준", "exp_candidate": "비교 전략"}
    regime_names = {"bull": "상승", "bear": "하락", "sideways": "횡보", "unknown": "미확인"}
    attempts = query_rows("SELECT * FROM buy_attempts ORDER BY observed_at DESC LIMIT 30", path=path)
    cases = query_rows("SELECT * FROM cases ORDER BY signal_day DESC,case_id DESC LIMIT 20", path=path)
    counts = query_rows("SELECT count(*) n,sum(complete) done,sum(error IS NOT NULL) errors FROM cases", path=path)
    counts = counts[0] if counts else {}
    rows = []
    for a in attempts:
        p = json.loads(a["payload"])
        for d in decisions(p):
            if d["reason"] == "no_candidates":
                continue
            rows.append([a["observed_at"][:19].replace("T", " "), profile_names.get(a["profile"], a["profile"]), d.get("name") or d["ticker"], LABELS[d["reason"]],
                         f"{d['additional_budget']:,}원" if 'additional_budget' in d else "확인 불가"])
    def table(headers, data):
        return "<table><thead><tr>" + "".join(f"<th>{escape(str(c))}</th>" for c in headers) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in data) + "</tbody></table>"
    result = ("<section><h2>미매수 사유·계좌 운용 관찰</h2><p class='muted'>실행 당시 조건과 실제 체결을 대조합니다. 미체결 세부 사유는 당시 예산 계산을 재현한 결과이며, 재현과 체결이 다르면 확인 필요로 표시합니다.</p>"
              + (table(["시각", "계좌", "종목", "결과·사유", "추가 가능 금액"], rows) if rows else "<p class='muted'>최근 30회 판단에 표시할 추천·체결 내역이 없습니다.</p>"))
    if attempts:
        result += f"<p class='muted'>최근 기록 {escape(attempts[0]['observed_at'][:19].replace('T', ' '))} · 추천 없는 점검도 기록됩니다.</p>"
    names = {"budget": "한도·최소 체결", "reentry": "매도 후 재진입", "rotation": "보유종목 교체", "sell": "매도 재분석"}
    labels = {"higher_cap": "한도 +10%p", "relaxed_min_fill": "최소 체결 10%", "wait_three": "3거래일 대기", "trend_reentry": "추세 회복", "rotate": "25% 교체", "avoid_stretched": "가격 부담 보류", "risk_sized": "위험 비중 조절", "SELL": "전량매도", "PARTIAL_AND_TRAIL": "부분익절·추적", "DEFER_ONE_SESSION": "1거래일 연장"}
    case_rows = []
    for c in cases:
        p, o = json.loads(c["payload"]), json.loads(c["outcomes"] or "{}")
        ctx = p["context"]
        diff = " · ".join(f"{labels[k]} {v:+.3f}%p" for k, v in o.get("10", {}).get("difference_pct", {}).items()) or "관찰 중"
        case_rows.append([c["signal_day"], names[c["kind"]], c["ticker"], regime_names.get(ctx.get("regime"), "미확인"), ctx["sector"], diff,
                          "수집 오류" if c["error"] else "수량 미달·집계 제외" if o.get("10", {}).get("skipped") else "완료" if c["complete"] else "관찰 중"])
    result += f"<p class='muted'>전체 비교 {counts.get('n', 0)}건 · 완료 {counts.get('done') or 0}건 · 오류 {counts.get('errors') or 0}건 · 최근 20건</p>"
    result += table(["판단일", "실험", "종목", "시장", "업종", "10거래일 기준 대비 차이", "상태"], case_rows) if case_rows else "<p class='muted'>조건에 해당하는 사례가 생기면 비교를 시작합니다.</p>"
    grouped = grouped_results(path)
    if grouped:
        result += "<details><summary>시장·업종별 완료 성과</summary><div class='details-body'>" + table(
            ["실험·비교안", "계좌", "시장", "업종", "완료 사례", "평균 차이"],
            [[names.get(r['experiment'].split(' / ')[0], '매수') + ' · ' + labels.get(r['experiment'].split(' / ')[1], r['experiment']), profile_names.get(r["profile"], r["profile"]), regime_names.get(r["regime"], "미확인"), r["sector"], r["count"], f"{r['mean_difference']:+.3f}%p"] for r in grouped]) + "</div></details>"
    else:
        result += "<p class='muted'>시장·업종별 집계는 판단 당시 분류가 연결된 10거래일 완료 사례부터 표시합니다.</p>"
    return result + "<p class='muted'>한도 비교는 단일 종목의 계좌 기여도, 재진입은 매도대금 수익률, 교체는 동일 자금 수익률입니다. 서로 합산할 수 없습니다. 국내 적극·중립 계좌의 관찰 전용이며 실제 주문은 바꾸지 않습니다. 시장은 당시 엔진 분류로, 자료 부족 시 횡보로 대체됐을 수 있습니다. 중복·겹치는 기간은 독립 표본이 아닙니다.</p></section>"


def grouped_results(path=PATH, entry_path="data/entry_research.db", sell_path="data/sell_research.db"):
    groups = {}
    def add(experiment, profile, ctx, value):
        key = (experiment, profile, ctx.get("regime", "unknown"), ctx.get("sector", "미분류"))
        groups.setdefault(key, []).append(value)
    for row in query_rows("SELECT * FROM cases WHERE complete=1 AND error IS NULL", path=path):
        p, o = json.loads(row["payload"]), json.loads(row["outcomes"] or "{}")
        if o.get("10", {}).get("skipped"):
            continue
        for variant, value in o.get("10", {}).get("difference_pct", {}).items():
            add(f"{row['kind']} / {variant}", row["profile"], p["context"], value)
    # Link the existing entry experiment by exact recorded recommendation run,
    # not by ticker alone or by retrospectively fetching today's classifications.
    contexts, execution_contexts = {}, {}
    for row in query_rows("SELECT payload FROM buy_attempts ORDER BY observed_at", path=path):
        p = json.loads(row["payload"])
        if p["profile"] != "aggressive":
            continue
        for trade in p.get("executions", []):
            if trade.get("price"):
                key = (trade["ticker"], p["observed_at"][:10], float(trade["price"]))
                execution_contexts.setdefault(key, []).append(context(p, trade["ticker"]))
        for pick in p["picks"]:
            ticker = pick["ticker"]
            run_id = p.get("evidence", {}).get(ticker, {}).get("run_id")
            if run_id:
                contexts.setdefault((run_id, ticker), context(p, ticker))
    for row in query_rows("SELECT payload,outcomes FROM entries WHERE state='COMPLETE' AND error IS NULL", path=entry_path):
        p, o = json.loads(row["payload"]), json.loads(row["outcomes"] or "{}")
        e = p["evidence"]
        ctx = contexts.get((e.get("run_id"), e["ticker"]))
        if ctx is None:
            ctx = {"regime": "unknown", "sector": "미분류"}
        for variant, value in o.get("horizons", {}).get("10", {}).get("difference_pct", {}).items():
            add(f"entry / {variant}", "aggressive", ctx, value)
    for row in query_rows("SELECT payload,outcomes FROM episodes WHERE complete=1 AND error IS NULL", path=sell_path):
        p, o = json.loads(row["payload"]), json.loads(row["outcomes"] or "{}")
        s = p["snapshot"]
        key = (s["ticker"], (s.get("entry_date") or "")[:10], float(s.get("entry_price") or 0))
        matches = [ctx for ctx in execution_contexts.get(key, []) if ctx["observed_at"] <= s["checked_at"]]
        ctx = matches[0] if len(matches) == 1 else {"regime": "unknown", "sector": "미분류"}
        if "10" in o:
            add(f"sell / {p['candidate_decision']}", "aggressive", ctx, o["10"]["difference_pct"])
    return [{"experiment": k[0], "profile": k[1], "regime": k[2], "sector": k[3], "count": len(v), "mean_difference": mean(v)} for k, v in sorted(groups.items())]


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
