"""Prospective exit comparisons on actual virtual-buy price paths; no orders."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/profit_research.db"
VERSION = "profit-protection-v1"
RULES = {"early_profit_pct": 5, "trail_percentage_points": 4, "fee_bps_each_side": 15}
LABELS = {"early": "+5% 절반 익절", "principal": "1차 익절 후 원금 보호", "trail": "1차 익절 후 고점 대비 4%p 청산"}
DIP_PATH = "data/profit_research_dips_v1.db"
DIP_VERSION = "dip-exit-v1"
DIP_RULES = {"thresholds_pct": {"zero": 0, "minus3": -3, "minus5": -5},
             "zero_requires_prior_profit": True, "fee_bps_each_side": 15}
DIP_LABELS = {"zero": "상승 후 0% 이하 복귀 청산", "minus3": "-3% 이하 조기 손절", "minus5": "-5% 이하 고정 손절"}


def simulate(trade, checks, sales, *, dips=False):
    """Extra signals fill at the next saved price; actual final exit ends all arms."""
    entry, quantity = float(trade["price"]), int(trade["quantity"])
    if not math.isfinite(entry) or entry <= 0 or quantity <= 0:
        raise ValueError("Invalid entry")
    tape = {}
    for row in checks:
        ts, price = row["checked_at"], float(row["close"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid observation price")
        if ts > trade["created_at"]:
            tape.setdefault(ts, {"sales": []})["price"] = price
    for sale in sales:
        ts, price = sale["created_at"], float(sale["price"])
        if ts < trade["created_at"] or not math.isfinite(price) or price <= 0 or int(sale["quantity"]) <= 0:
            raise ValueError("Invalid sale")
        event = tape.setdefault(ts, {"sales": []})
        event["price"] = price
        event["sales"].append(sale)
    strategies = DIP_LABELS if dips else LABELS
    fee = (DIP_RULES if dips else RULES)["fee_bps_each_side"] / 10000
    arms = {k: {"remaining": quantity, "cash": 0.0, "pending": 0, "early_taken": False, "exits": []}
            for k in ("baseline", *strategies)}
    first_at, peak, mark, last_at = None, None, entry, trade["created_at"]
    had_profit = False

    def sell(arm, amount, price, ts, reason):
        amount = min(amount, arm["remaining"])
        if amount:
            arm["remaining"] -= amount
            arm["cash"] += amount * price * (1 - fee)
            arm["exits"].append({"at": ts, "price": price, "quantity": amount, "reason": reason})

    for ts, event in sorted(tape.items()):
        mark, last_at = event["price"], ts
        ret = (mark / entry - 1) * 100
        for key in strategies:
            arm = arms[key]
            if arm["pending"]:
                sell(arm, arm["pending"], mark, ts, key)
                arm["pending"] = 0
                if dips:
                    arm["extra_exit_at"] = ts
                if key == "early":
                    arm["early_taken"] = True
            if dips and arm.get("extra_exit_at") and ts > arm["extra_exit_at"]:
                arm["post_exit_max_return_pct"] = max(arm.get("post_exit_max_return_pct", -math.inf), ret)
        for sale in event["sales"]:
            if int(sale["quantity"]) > arms["baseline"]["remaining"]:
                raise ValueError("Sales exceed bought quantity")
            sell(arms["baseline"], int(sale["quantity"]), float(sale["price"]), ts, sale.get("reason") or "기존 매도")
            if sale.get("stage") == "take_profit_1":
                first_at, peak = ts, ret
            for key in strategies:
                if key != "early":
                    sell(arms[key], int(sale["quantity"]), float(sale["price"]), ts, sale.get("reason") or "기존 매도")
            if not arms["baseline"]["remaining"]:
                for key in strategies:
                    sell(arms[key], arms[key]["remaining"], float(sale["price"]), ts, "기존 최종 매도")
        if not arms["baseline"]["remaining"]:
            break
        if dips:
            for key, threshold in DIP_RULES["thresholds_pct"].items():
                arm = arms[key]
                if arm["remaining"] and not arm.get("signal_at") and ret <= threshold + 1e-9 and (key != "zero" or had_profit):
                    arm["pending"] = arm["remaining"]
                    arm["signal_at"] = ts
                    arm["trigger_return_pct"] = ret
            had_profit = had_profit or ret > 1e-9
            continue
        early = arms["early"]
        if not early["early_taken"] and quantity >= 2 and ret >= RULES["early_profit_pct"]:
            early["pending"] = quantity // 2
        if first_at and ts > first_at:
            peak = max(peak, ret)
            if ret <= 0:
                arms["principal"]["pending"] = arms["principal"]["remaining"]
            if peak - ret >= RULES["trail_percentage_points"]:
                arms["trail"]["pending"] = arms["trail"]["remaining"]
    for arm in arms.values():
        value = arm["cash"] + arm["remaining"] * mark * (1 - fee)
        arm["net_return_pct"] = (value / (entry * quantity * (1 + fee)) - 1) * 100
    return {"as_of": last_at, "complete": arms["baseline"]["remaining"] == 0,
            "observations": len(tape), "first_take_profit_at": first_at, "had_profit": had_profit, "arms": arms}


def connect(path=PATH, *, version=VERSION, rules=None):
    rules = RULES if rules is None else rules
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cases(trade_id INTEGER PRIMARY KEY,ticker TEXT NOT NULL,
            bought_at TEXT NOT NULL,payload TEXT NOT NULL,outcomes TEXT,complete INTEGER NOT NULL DEFAULT 0,
            error TEXT,updated_at TEXT);
    """)
    for key, value in (("activated_at", datetime.now().isoformat(timespec="seconds")),
                       ("version", version), ("rules", json.dumps(rules, sort_keys=True))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != version or json.loads(saved["rules"]) != rules:
        db.close()
        raise ValueError("Profit research rules changed; create a new versioned database")
    db.commit()
    return db


def run(source=DB_PATH, path=PATH, *, dips=False):
    """Freeze new buys only; replay their saved observations without network calls."""
    version, rules = (DIP_VERSION, DIP_RULES) if dips else (VERSION, RULES)
    with closing(connect(path, version=version, rules=rules)) as db, closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as src:
        src.row_factory = sqlite3.Row
        start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
        added, updated, failures = 0, 0, 0
        for trade in src.execute("SELECT * FROM virtual_trades WHERE created_at>=? ORDER BY created_at,trade_id", (start,)):
            added += db.execute("INSERT OR IGNORE INTO cases(trade_id,ticker,bought_at,payload) VALUES (?,?,?,?)",
                                (trade["trade_id"], trade["ticker"], trade["created_at"], json.dumps(dict(trade), ensure_ascii=False))).rowcount
        for case in db.execute("SELECT * FROM cases WHERE complete=0").fetchall():
            try:
                trade = json.loads(case["payload"])
                next_buy = src.execute("SELECT min(created_at) FROM virtual_trades WHERE ticker=? AND trade_id>?",
                                       (trade["ticker"], trade["trade_id"])).fetchone()[0]
                end = next_buy or "9999"
                params = (trade["ticker"], trade["created_at"], end)
                checks = [dict(r) for r in src.execute("""SELECT DISTINCT checked_at,close FROM position_checks
                    WHERE ticker=? AND checked_at>? AND checked_at<? AND close>0 AND return_pct IS NOT NULL
                    ORDER BY checked_at""", params)]
                sales = [dict(r) for r in src.execute("""SELECT * FROM virtual_sales
                    WHERE ticker=? AND created_at>=? AND created_at<? ORDER BY created_at,sale_id""", params)]
                result = simulate(trade, checks, sales, dips=dips)
                if next_buy and not result["complete"]:
                    raise ValueError("Next buy overlaps an unclosed position; manual review required")
                db.execute("UPDATE cases SET outcomes=?,complete=?,error=NULL,updated_at=? WHERE trade_id=?",
                           (json.dumps(result, ensure_ascii=False), int(result["complete"]), datetime.now().isoformat(timespec="seconds"), case["trade_id"]))
                updated += 1
            except Exception as error:
                failures += 1
                db.execute("UPDATE cases SET error=?,updated_at=? WHERE trade_id=?",
                           (str(error), datetime.now().isoformat(timespec="seconds"), case["trade_id"]))
        db.commit()
    return {"version": version, "added": added, "updated": updated, "failures": failures}


def run_dips(source=DB_PATH, path=DIP_PATH):
    return run(source, path, dips=True)


def dip_dashboard_data(path=DIP_PATH):
    cases = query_rows("SELECT * FROM cases ORDER BY bought_at DESC,trade_id DESC", path=path)
    meta = query_rows("SELECT value FROM metadata WHERE key='activated_at'", path=path)
    completed = [(json.loads(r["payload"]), json.loads(r["outcomes"])) for r in cases
                 if r["complete"] and not r["error"] and r["outcomes"]]
    results, recovery = [], []
    for key, label in DIP_LABELS.items():
        differences, money = [], []
        triggered, exited, followed = 0, 0, []
        for trade, outcome in completed:
            arm = outcome["arms"][key]
            diff = arm["net_return_pct"] - outcome["arms"]["baseline"]["net_return_pct"]
            differences.append(diff)
            money.append(float(trade["price"]) * int(trade["quantity"]) *
                         (1 + DIP_RULES["fee_bps_each_side"] / 10000) * diff / 100)
            triggered += bool(arm.get("signal_at"))
            exited += bool(arm.get("extra_exit_at"))
            if "post_exit_max_return_pct" in arm:
                followed.append(arm["post_exit_max_return_pct"])
        results.append([label, str(len(completed)), str(triggered), str(exited),
                        f"{sum(differences)/len(differences):+.3f}%p" if differences else "관찰 중",
                        f"{sum(v>1e-9 for v in differences)}/{len(differences)}" if differences else "—"])
        recovery.append([label, f"{sum(v for v in money if v>0):+,.0f}원" if money else "관찰 중",
                         f"{sum(v for v in money if v<0):+,.0f}원" if money else "관찰 중", str(len(followed)),
                         *[f"{sum(v>=target-1e-9 for v in followed)}/{len(followed)} · {sum(v>=target-1e-9 for v in followed)/len(followed)*100:.1f}%"
                           if followed else "후속 관측 없음" if exited else "해당 없음" if completed else "관찰 중" for target in (10, 20)]])
    return {"start": meta[0]["value"] if meta else "수집 준비 중", "total": len(cases),
            "complete": len(completed), "errors": sum(bool(r["error"]) for r in cases),
            "plans": [[DIP_LABELS["zero"], "양수 수익률 관측 후 0% 이하 복귀 시 남은 물량 청산"],
                      [DIP_LABELS["minus3"], "매수가 대비 -3% 이하 도달 시 남은 물량 청산"],
                      [DIP_LABELS["minus5"], "매수가 대비 -5% 이하 도달 시 남은 물량 청산"]],
            "results": results, "recovery": recovery}


def historical_dashboard_data(path="reports/current_data_experiment/results.json"):
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        performance = [[r['label'], str(r['n']), f"{r['mean_pct']:+.3f}%", f"{r['median_pct']:+.3f}%",
                        f"{r['win_rate_pct']:.1f}%", f"{r['worst_pct']:+.3f}%", f"{r['mean_difference_pctp']:+.3f}%p",
                        f"{r['improved']}/{r['neutral']}/{r['worsened']}"] for r in report['performance']]
        thresholds = [[f"{r['threshold_pct']}% 이하", f"{r['hit']}/{report['included']} ({r['of_all_pct']:.1f}%)",
                       str(r['later_above_entry']), f"{r['profitable_exit']} ({r['profitable_of_hit_pct']:.1f}%)",
                       f"{r['loss_exit']} ({r['loss_of_hit_pct']:.1f}%)"] for r in report['threshold_outcomes']]
        handling = [["체결" if r['tp1'] else "미체결", r['stage'], r['reason'], str(r['count'])] for r in report['final_sale_handling']]
        return {"available": True, "as_of": report['generated_at'], "included": report['included'],
                "excluded": sum(report['excluded'].values()), "performance": performance, "thresholds": thresholds, "handling": handling}
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False}


def target_bear_dashboard_data(path="reports/target_bear_research/results.json"):
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        targets = [[r['label'], f"{r['count']}/{r['denominator']}", f"{r['pct']:.1f}%" if r['pct'] is not None else '자료 없음'] for r in report['target_groups']]
        handling = [[r['label'], v['stage'], v['reason'], str(v['count'])] for r in report['target_groups'] for v in r['final_sales']]
        bears = [[r['label'], str(r['n']), f"{r['mean_pct']:+.3f}%" if r['mean_pct'] is not None else '—',
                  f"{r['mean_difference_pctp']:+.3f}%p" if r['mean_difference_pctp'] is not None else '—',
                  f"{r['worst_pct']:+.3f}%" if r['worst_pct'] is not None else '—',
                  f"{r['improved']}/{r['neutral']}/{r['worsened']}"] for r in report['bear_comparisons']]
        return {'available':True, 'as_of':report['generated_at'], 'targets':targets, 'handling':handling, 'bears':bears,
                'unknown':report['bear_unknown']}
    except (OSError, ValueError, KeyError, TypeError):
        return {'available':False}


def bear_account_dashboard_data(path="reports/bear_account_research/results.json"):
    labels={'baseline':'기존 매수 기회','half':'하락장 매수 수량 절반','skip':'하락장 매수 보류','resume':'보류 + 회복 추천 재진입(7종목 한도)'}
    try:
        report=json.loads(Path(path).read_text(encoding='utf-8'))
        rows=[];windows=[];opportunities=[]
        for key,account in report['accounts'].items():
            m=account['metrics']
            rows.append([labels[key],f"{m['twr_pct']:+.3f}%",f"{m['mdd_pct']:.3f}%",f"{m['equity']:,.0f}원",
                         f"{m['average_cash_pct']:.1f}%",f"{m['cash_pct']:.1f}%",str(m['cash_clipped_buys']),str(m['holding_limited_buys'])])
        for window in report['recovery']:
            for key,m in window['accounts'].items():
                windows.append([window['recovery'],window['end'],labels[key],f"{m['window_twr_pct']:+.3f}%",f"{m['window_mdd_pct']:.3f}%"])
            opportunities.append([window['recovery'],f"{window['budget']:,.0f}원",str(window['eligible_signal_rows']),str(window['reentry_count']),
                                  f"{window['reentry_net_pnl']:+,.0f}원",f"{window['resume_minus_skip_equity']:+,.0f}원"])
        return {'available':True,'start':report['start'],'cutoff':report['cutoff'],'as_of':report['generated_at'],
                'rows':rows,'windows':windows,'opportunities':opportunities,'failed_signals':report['excluded_failed_signal_rows']}
    except (OSError,ValueError,KeyError,TypeError):
        return {'available':False}


def dashboard_data(path=PATH, dip_path=DIP_PATH):
    from .research_extensions import dashboard_data as extension_data
    cases = query_rows("SELECT * FROM cases ORDER BY bought_at DESC,trade_id DESC", path=path)
    meta = query_rows("SELECT value FROM metadata WHERE key='activated_at'", path=path)
    start = meta[0]["value"] if meta else "수집 준비 중"
    plans = [
        ["하락장 제안 ① 신규 매수 축소", "과거 거래·계좌 원장 비교", "기존 매수 vs 신규 매수 수량 절반(정수 내림). 기존 매도 기록을 비례 배분"],
        ["하락장 제안 ② 신규 매수 중단", "과거 거래·계좌 원장 비교", "기존 매수 vs 신규 매수 보류·무이자 현금 유지. 회복 추천 재진입은 별도 고정 비교"],
        ["하락장 제안 ③ 익절 앞당김", "거래별 과거 비교 · 계좌 실험 미실행", "매수는 동일하게 두고 +5% 절반 익절 추가를 비교. 매수 축소와 동시 적용하지 않음"],
        [LABELS["early"], "관찰 등록", "기존 가상 체결 vs +5%에서 정수수량 절반 익절. 잔량은 기존 최종 매도 시점에 청산"],
        [LABELS["principal"], "관찰 등록", "기존 1차 익절 체결 이후 잔량에 매수가 청산선 추가. 비용을 포함한 본전 보장은 아님"],
        [LABELS["trail"], "관찰 등록", "기존 1차 익절 체결 이후 수익률 고점 대비 4%p 하락하면 잔량 청산"],
        ["가격 부담 시 매수 보류", "기존 연구 재사용", "추천 당시 20일선 이격÷ATR ≥2이면 보류하는 기존 매수 관찰과 연결"],
    ]
    results = []
    for key, label in LABELS.items():
        eligible = [json.loads(r["outcomes"]) for r in cases if r["complete"] and not r["error"] and r["outcomes"]]
        if key != "early":
            eligible = [o for o in eligible if o["first_take_profit_at"]]
        differences = [o["arms"][key]["net_return_pct"] - o["arms"]["baseline"]["net_return_pct"] for o in eligible]
        results.append([label, str(len(differences)), f"{sum(differences)/len(differences):+.3f}%p" if differences else "관찰 중",
                        f"{sum(v>1e-9 for v in differences)}/{len(differences)}" if differences else "—"])
    return {"start": start, "plans": plans, "results": results,
            "total": len(cases), "complete": sum(bool(r["complete"]) for r in cases),
            "errors": sum(bool(r["error"]) for r in cases), "dips": dip_dashboard_data(dip_path),
            "extensions": extension_data(), "historical": historical_dashboard_data(), "targetBear": target_bear_dashboard_data(),
            "bearAccount": bear_account_dashboard_data()}


def dashboard_section(path=PATH):
    from .research_extensions import dashboard_section as extension_section
    data = dashboard_data(path)
    dips = data["dips"]

    def table(heads, rows):
        return "<table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in heads) + "</tr></thead><tbody>" + "".join(
            "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>" for row in rows) + "</tbody></table>"

    historical = data['historical']
    history_html = "<section><h2>현재 자료 실험 결과 · 과거 탐색</h2>"
    if historical['available']:
        history_html += (f"<p class='muted'>분석 {historical['included']}건 · 제외 {historical['excluded']}건 · 분석 시점 {escape(historical['as_of'])}</p>"
                         + table(["청산 규칙", "표본", "평균 순수익률", "중앙값", "이익 거래 비율", "최저 거래", "기존 대비", "개선/동일/악화"], historical['performance'])
                         + table(["하락 기준", "해당/전체", "이후 매수가 초과 관측", "최종 합산 이익", "최종 합산 손실"], historical['thresholds'])
                         + table(["1차 익절", "마지막 매도 단계", "마지막 매도 사유", "건수"], historical['handling'])
                         + "<p class='muted'>과거 저장 가격으로 계산한 거래별 동일 가중 평균이며 계좌 수익률이 아닙니다. 신규 전향 연구와 별도입니다. 비용은 매수·매도 각 0.15%, 추가 신호는 다음 저장 가격에 체결하며 기존 최종 매도 때 종료합니다. 하락 집단은 중복되며 이익·손실 비율은 해당 집단 기준입니다. 0% 하락 집단은 선행 상승을 요구하지 않지만 0% 복귀 청산 실험은 요구합니다. 손실 종료가 모두 손절 사유라는 뜻은 아닙니다. 실제 호가 체결·관측 사이 고저가·목표가 미달 여부는 검증하지 않았습니다. 자동 주문에는 적용하지 않습니다.</p>")
    else:
        history_html += "<p class='muted'>과거 탐색 결과 파일이 없거나 읽을 수 없습니다.</p>"
    history_html += "</section>"
    extra = data['targetBear']
    if extra['available']:
        history_html += ("<section><h2>목표 도달 진단 · 하락장 과거 비교</h2>"
                         + f"<p class='muted'>분석 시점 {escape(extra['as_of'])} · 하락장 판정 불가 {extra['unknown']}건</p>"
                         + table(["목표 도달 조건", "해당/분모", "비율"], extra['targets'])
                         + table(["조건", "마지막 매도 단계", "마지막 매도 사유", "건수"], extra['handling'])
                         + table(["하락장 규칙", "표본", "원래 배정자금 대비 평균 순손익", "기존 대비", "최저 거래 기여", "개선/동일/악화"], extra['bears'])
                         + "<p class='muted'>+10%·+20%는 고정 진단 기준이며 미확인 과거 목표 설정을 소급 확정한 값이 아닙니다. 미관측은 장중 미도달의 증명이 아닙니다. 실제 익절 여부는 체결 기록을 사용합니다. 하락장은 직전 거래일 종가·MA20이 모두 MA60 아래인 경우입니다. 매수 절반은 정수 내림하고 분할 매도는 누적 비례 내림합니다. 위 거래별 표는 다른 종목 재투자·계좌 최대낙폭·전체 현금 비중·회복 후 기회비용을 포함하지 않습니다. 계좌 비교는 별도 연구입니다. 비용은 매수·매도 각 0.15%이며 자동 매매는 변경하지 않았습니다.</p></section>")
    account = data['bearAccount']
    if account['available']:
        history_html += ("<section><h2>하락장 대체 계좌 · 회복 후 20거래일</h2>"
                         + f"<p class='muted'>비교 조건: 네 계좌 공통 최대 7종목 · 기간 {escape(account['start'])}–{escape(account['cutoff'])} · 분석 {escape(account['as_of'])} · 실패 실행 추천 제외 {account['failed_signals']}행</p>"
                         + table(["계좌", "입금 영향 제외 TWR", "일별 최대낙폭", "마지막 자산", "평균 현금 비중", "마지막 현금 비중", "현금 부족 축소", "보유 한도 보류"], account['rows'])
                         + table(["회복 확인", "20거래일 종료", "계좌", "구간 TWR", "구간 최대낙폭"], account['windows'])
                         + table(["회복 확인", "재진입 예산", "적격 추천 행", "재진입 종목", "추가 물량 순손익", "보류 대비 구간 종료 자산 차이"], account['opportunities'])
                         + "<p class='muted'>실제 매수 기회를 고정한 계좌 재현이며 전체 매수 엔진을 재실행한 전략 백테스트가 아닙니다. 매수·매도 각 0.15% 비용과 현금 제약을 적용합니다. TWR은 입금 영향을 제외한 시간가중수익률, 최대낙폭은 일별 종가 기준입니다. 회복 재진입은 완료된 적극투자형 추천의 다음 거래일 시가에, 회복일 보류-기존 현금 차이의 1/7씩 매수해 20번째 거래일 종가에 청산하는 고정 벤치마크입니다. 기존·축소·보류·재진입 네 계좌 모두 동일한 전체 7종목 한도를 적용합니다. 보류와 재진입의 차이는 같은 한도에서 재진입 정책을 추가한 영향입니다. 추가 물량 손익과 계좌 차이는 이후 원래 매수의 제약 효과 때문에 다를 수 있습니다. 한도 없는 원장 감사는 실험 대조군과 별도입니다. 현재일 장중 자료와 실패 실행 추천은 제외했으며 실제 체결·슬리피지·기업행동 조정은 검증하지 않았습니다.</p></section>")
    return (history_html + "<section><h2>하락장 대응 제안 · 매수·매도 추가 실험</h2>"
            "<p class='muted'>매수 축소·보류는 과거 거래·계좌 원장 비교를 표시하며 익절 앞당김은 거래별 비교입니다. 직전 완료 거래일 KOSPI 종가가 60일선 아래이고 "
            "20일선도 60일선 아래인 날을 하락장으로 정하는 안입니다. 정보가 부족한 날은 제외하며, "
            "회복 뒤 20거래일도 관찰해 놓친 반등·현금 비중·계좌 최대낙폭을 비교합니다.</p>"
            + table(["연구", "상태", "비교 규칙"], data["plans"])
            + f"<p class='muted'>관찰 시작 {escape(data['start'])} · 신규 가상매수 {data['total']}건 · 종료 {data['complete']}건 · 오류 {data['errors']}건</p>"
            + table(["실험", "종료 표본", "평균 순수익률 차이", "기존보다 유리한 건수"], data["results"])
            + "<p class='muted'>국내 적극투자형 신규 가상매수만 관찰합니다. 추가 신호는 다음 저장 가격에 체결한다고 가정하며 "
            "갭 하락을 반영합니다. 기존 실제 가상 체결을 대조군으로 쓰고, 기존 전량매도 시점에 비교를 종료합니다. "
            "매수·매도 비용 각 0.15% · 두 매도 물량의 손익 합산 · 완료 건만 평균에 포함. "
            "원금 보호·추적 청산은 1차 익절 체결 건만 집계합니다. 계좌 수익률·최대낙폭을 계산하는 연구는 아니며 "
            "자동 주문에는 적용하지 않습니다. 점검 사이 미관측 가격은 확인할 수 없습니다.</p></section>"
            "<section><h2>0%·-3%·-5% 하락 기준 독립 실험</h2>"
            + table(["실험", "추가 청산 조건"], dips["plans"])
            + f"<p class='muted'>별도 시작 {escape(dips['start'])} · 신규 가상매수 {dips['total']}건 · 완료 {dips['complete']}건 · 오류 {dips['errors']}건</p>"
            + table(["실험", "완료 대조 표본", "조건 발생", "추가 청산 체결", "평균 순수익률 차이", "기존보다 유리한 건수"], dips["results"])
            + table(["실험", "개선 손익 합계", "악화 손익 합계", "청산 후 관측 표본", "이후 +10% 도달", "이후 +20% 도달"], dips["recovery"])
            + "<p class='muted'>같은 신규 매수 건에서 세 규칙을 독립 비교합니다. 기존 부분 익절은 유지하고 남은 물량만 청산합니다. "
            "신호 다음 저장 가격 체결·매수/매도 비용 각 0.15%·기존 최종 매도까지 비교하며 완료·오류 없는 건만 집계합니다. "
            "평균 차이의 분모는 조건 미발생을 포함한 모든 완료 대조 표본입니다. 개선/악화는 거래별 기존 대비 순손익 차이의 양수/음수 합이며 "
            "계좌 수익률이 아닙니다. 회복 비율은 추가 청산 이후 기존 매도까지 후속 가격이 있는 표본만 대상으로 하며 목표는 최초 매수가 대비입니다. "
            "매수가 복귀 청산은 비용·갭 때문에 본전을 보장하지 않습니다. 별도 시작 이전 거래는 소급 집계하지 않습니다.</p></section>"
            + extension_section())


if __name__ == "__main__":
    print(json.dumps({"profit": run(), "dips": run_dips()}, ensure_ascii=False))
