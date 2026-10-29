"""Read-only data for the dashboard v2 preview."""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from statistics import mean, stdev

from .dashboard import actionable_issue_rows, display_value, experiment_account_rows, experiment_progress_rows, held_or_sold_today_tickers, home_sell_rows, latest_operational_status, real_account_state, recommendation_tracking_rows, time_weighted_returns, today_recommendation_rows, today_sell_alert_rows, user_run_rows
from .data_store import query_rows, recent_position_checks, recent_price_quality, recent_virtual_sales, virtual_trader_state
from .market_summary import _read_us_cache
from .trading_profiles import PROFILES
from .account_valuation import value_saved_account, price_after_entry as _price_after_entry
from .us_dashboard import STALE_DAYS, STATE_LABELS, max_drawdown_pct, read_csv, read_ledger, read_strategy
from .us_strategy_account import ACCOUNTS


def _money(value: float | int | None, market: str, signed: bool = False) -> str:
    if value is None or value == "":
        return "기록 없음"
    symbol = "₩" if market == "kr" else "$"
    try:
        return f"{symbol}{float(value):+,.0f}" if signed else f"{symbol}{float(value):,.0f}"
    except (TypeError, ValueError):
        return "기록 없음"


def _pct(value: float | str | None) -> str:
    try:
        return f"{float(value):+,.2f}%"
    except (TypeError, ValueError):
        return "기록 없음"


def real_account_payload() -> dict:
    """Expose only domestic balance and holdings, without account identifiers or orders."""
    state = real_account_state()
    if not state.get("connected"):
        reason = str(state.get("reason") or "")
        if "TOSS_CLIENT_ID" in reason or "TOSS_CLIENT_SECRET" in reason:
            reason = "토스증권 인증 정보가 없습니다."
        elif reason != "연결된 계좌 없음":
            reason = "토스증권 실계좌를 조회하지 못했습니다."
        return {"connected": False, "reason": reason}
    total = state["total_equity"]
    invested = state["market_value"] - state["profit_loss"]
    rows = []
    for item in state["holdings"]:
        valuation = item.get("valuation")
        rows.append([
            f"{item.get('name') or item.get('ticker') or '—'} ({item.get('ticker') or '—'})",
            str(item.get("quantity") or "—"),
            _money(item.get("average_price"), "kr"),
            _money(item.get("current_price"), "kr"),
            _money(valuation, "kr"),
            _money(item.get("profit_loss"), "kr", True),
            _pct(item.get("return_pct")),
            str(item.get("watch_state") or "보유 중"),
        ])
    return {"connected": True, "asOf": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
            "asset": _money(total, "kr"), "cash": _money(state["cash"], "kr"),
            "marketValue": _money(state["market_value"], "kr"),
            "unrealized": _money(state["profit_loss"], "kr", True),
            "returnPct": _pct(state["profit_loss"] / invested * 100) if invested > 0 else "기록 없음",
            "hold": f"{len(rows)}종목", "rows": rows}


def _kr_account(key: str, name: str, include_sales: bool = True) -> dict:
    path = PROFILES[key]["db_path"]
    if not Path(path).exists():
        return {"name": name, "kind": "가상매매", "asset": "기록 없음", "daily": "기록 없음", "cash": "기록 없음", "marketValue": "기록 없음", "unrealized": "기록 없음", "hold": "0종목", "returnPct": "기록 없음", "rows": []}
    state = virtual_trader_state(path=path)
    snapshots = query_rows("SELECT created_at, cash, valuation, equity, profit_loss FROM virtual_valuation_snapshots ORDER BY snapshot_id DESC LIMIT 2", path=path)
    last = snapshots[0] if snapshots else None
    recent_change = float(last["equity"]) - float(snapshots[1]["equity"]) if len(snapshots) == 2 else None
    saved_checks = recent_position_checks(1000, path=path)
    saved_quality = recent_price_quality(100, path=path)
    state = value_saved_account(state, saved_checks, saved_quality)
    total = state['total_equity']
    checks = {}
    for check in saved_checks:
        checks.setdefault(check["ticker"], check)
    rows = []
    price_issues = []
    for h in state["holdings"]:
        check = checks.get(h["ticker"], {})
        entry_at = str(h.get("first_entry_at") or "")
        checked_at = str(check.get("checked_at") or "")
        if not _price_after_entry(checked_at, entry_at):
            check = {}
            checked_at = ""
        price = h['current_price']
        price_at = h['price_observed_at']
        cost = float(h["cost"])
        valuation, profit = h['valuation'], h['profit_loss']
        try:
            holding_days = f"{(date.today() - date.fromisoformat(entry_at[:10])).days}일"
        except ValueError:
            holding_days = "기록 없음"
        if price is None:
            price_issues.append(h["name"] or h["ticker"])
        status = ("매도조건 충족" if check.get("decision") == "SELL" else
                  "1차 익절 완료" if h.get("position_status") == "partial" else
                  "정상 보유" if check else "점검 기록 없음")
        stop_pct = float(check.get("dynamic_stop_loss_pct") or -5) if check else None
        stop = _money(round(float(h["average_price"]) * (1 + stop_pct / 100)), "kr") if stop_pct is not None else "기록 없음"
        rows.append([f"{h['name'] or h['ticker']} ({h['ticker']})", str(h["quantity"]),
                     _pct(valuation / total * 100) if valuation is not None and total else "기록 없음",
                     holding_days, _money(h["average_price"], "kr"), _money(price, "kr"),
                     price_at.replace("T", " ")[:16] or "기록 없음",
                     _money(profit, "kr", True), _pct(profit / cost * 100) if profit is not None and cost else "기록 없음",
                     status, stop, _money(check.get("ma20"), "kr")])
    sales = []
    for sale in recent_virtual_sales(500, path=path) if include_sales else []:
        basis = float(sale.get("cost_basis") or 0)
        realized = float(sale.get("realized_profit_loss") or 0)
        sales.append([str(sale.get("created_at") or "").replace("T", " ")[:16],
                      sale.get("name") or sale.get("ticker") or "—",
                      "부분매도" if sale.get("sale_type") == "partial" else "전량매도",
                      str(sale.get("quantity") or "—"), _money(sale.get("price"), "kr"),
                      _money(realized, "kr", True), _pct(realized / basis * 100) if basis else "기록 없음",
                      sale.get("reason") or "—"])
    return {"name": name, "kind": "가상매매 · 현재 잔량 × 최신 저장가격", "asset": _money(total, "kr"), "daily": _money(recent_change, "kr", True), "cash": _money(state.get("cash", 0), "kr"), "marketValue": _money(state["holdings_value"], "kr"), "unrealized": _money(state["unrealized_profit_loss"], "kr", True), "hold": f"{len(rows)}종목", "returnPct": _pct(state['total_return_pct']), "priceAsOf": state['price_updated_at'].replace("T", " ")[:16] or "기록 없음", "priceOldestAt": state['price_oldest_at'].replace("T", " ")[:16] or "기록 없음", "priceIssues": price_issues, "realized": _money(state["realized_profit_loss"], "kr", True), "sales": sales, "rows": rows}


def _us_account(account, data: dict | None = None) -> dict:
    data = data if data is not None else read_strategy(account)
    equity = data["equity"]
    if not equity:
        return {"name": account.label, "kind": "가상매매", "asset": "기록 없음", "daily": "기록 없음", "cash": "기록 없음", "marketValue": "기록 없음", "unrealized": "기록 없음", "hold": "0종목", "returnPct": "기록 없음", "rows": []}
    last = equity[-1]
    asset, cash = float(last["equity_usd"]), float(last["cash_usd"])
    daily = asset - float(equity[-2]["equity_usd"]) if len(equity) > 1 else None
    rows = [[data["names"].get(h["ticker"], h["ticker"]), f"{float(h['shares']) * float(h['last_usd']) / asset * 100:.1f}%" if asset else "—", "—", _pct(h.get("return_pct")), "보유 중"] for h in data["holdings"]]
    return {"name": account.label, "kind": "가상매매", "asset": _money(asset, "us"), "daily": _money(daily, "us", True), "cash": _money(cash, "us"), "marketValue": _money(asset - cash, "us"), "unrealized": "기록 없음", "hold": f"{len(rows)}종목", "returnPct": _pct(last.get("return_pct")), "rows": rows}


def _index_cache(symbol: str) -> list[tuple[str, float]]:
    files = sorted(Path(".cache/naver").glob(f"{symbol}-*.json"), key=lambda p: p.stem.rsplit("-", 1)[-1], reverse=True)
    best: list[tuple[str, float]] = []
    for path in files[:30]:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            points = [(str(row[0]), float(row[4])) for row in rows if len(row) > 4]
            if len(points) > 1 and (not best or points[-1][0] > best[-1][0] or (points[-1][0] == best[-1][0] and len(points) > len(best))):
                best = points
        except (OSError, ValueError, TypeError, IndexError):
            continue
    return best


def _market_entry(name: str, points: list[tuple[str, float]]) -> list[str]:
    if len(points) < 2:
        return [name, "기록 없음", "—"]
    return [name, f"{points[-1][1]:,.2f}", _pct((points[-1][1] / points[-2][1] - 1) * 100)]


def _signal_history(market: str | None = None, strategies: list[dict] | None = None) -> tuple[list[dict], list[dict]] | list[dict]:
    if market == "us":
        kr = []
    else:
        tickers = {(str(row.get("created_at") or "")[:10], row.get("name")): row.get("ticker") or ""
                   for row in read_csv("logs/recommendations.csv")}
        kr = [{"date": row["pick_date"], "name": row["name"], "ticker": tickers.get((row["pick_date"], row["name"]), ""),
           "signal": "매수 추천", "reason": f"추천 점수 {row['score']}" if row.get("score") else "추천 기록",
           "status": row["tracking_status"], "returnPct": _pct(row.get("return_pct")), "source": "국내 추천",
           "detail": {"entryPrice": _money(row.get("entry_price"), "kr"), "currentPrice": _money(row.get("current_price"), "kr"),
                      "virtualBought": row.get("virtual_bought") or "기록 없음", "sellDate": row.get("sell_alert_date") or "기록 없음",
                      "sellPrice": _money(row.get("sell_alert_price"), "kr"), "sellReturn": _pct(row.get("sell_alert_return_pct")),
                      "sellReason": row.get("sell_reason") or "기록 없음"}}
              for row in recommendation_tracking_rows()]
        kr += [{"date": str(row.get("created_at") or ""), "name": row.get("name") or row.get("ticker") or "—",
            "ticker": row.get("ticker") or "", "signal": "매도 신호",
            "reason": row.get("summary") or row.get("reason") or "매도 알림",
            "status": {"full": "전량 매도 검토", "partial": "부분 매도 검토"}.get(row.get("sale_type"), "알림"),
            "returnPct": _pct(row.get("return_pct")), "source": "국내 매도",
            "detail": {"sellDate": str(row.get("created_at") or "")[:10],
                       "sellPrice": _money(row.get("close"), "kr")}}
               for row in read_csv("logs/sell_alerts.csv")]
    us = []
    for index, account in enumerate(ACCOUNTS if market != "kr" else ()):
        names = (strategies[index] if strategies is not None else read_strategy(account))["names"]
        us += [{"date": row.get("date") or "", "name": names.get(row.get("ticker"), row.get("ticker") or "—"),
                "ticker": row.get("ticker") or "", "signal": "전략 매수" if row.get("event") == "buy" else "전략 매도",
                "reason": row.get("reason") or "전략 결정", "status": "장부 기록", "returnPct": "기록 없음", "source": account.label,
                "detail": {"decisionPrice": _money(row.get("price_usd"), "us")}}
               for row in read_csv(account.path("decisions.csv")) if row.get("event") in {"buy", "sell"}]
    kr.sort(key=lambda row: row["date"], reverse=True)
    us.sort(key=lambda row: row["date"], reverse=True)
    return kr if market == "kr" else us if market == "us" else (kr, us)


def _history_source_signature(market: str) -> tuple:
    if market == "kr":
        paths = ("logs/recommendations.csv", "logs/recommendation_performance.csv", "logs/sell_alerts.csv",
                 "logs/positions_report.csv", "data/positions.csv", "data/stock_alarm.db", "data/stock_alarm.db-wal")
    else:
        paths = tuple(account.path(name) for account in ACCOUNTS for name in ("decisions.csv", "universe.csv"))
    signature = []
    for name in paths:
        try:
            stat = Path(name).stat()
            signature.append((str(name), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
        except OSError:
            signature.append((str(name), None, None, None, None))
    return tuple(signature)


@lru_cache(maxsize=8)
def _cached_signal_history(market: str, signature: tuple) -> list[dict]:
    return _signal_history(market)


def _comparison_chart(curves: list[tuple[str, dict[str, float]]]) -> dict:
    if not curves or any(len(points) < 2 for _, points in curves):
        return {"days": [], "series": []}
    days = sorted(set.intersection(*(set(points) for _, points in curves)))[-90:]
    if len(days) < 2:
        return {"days": [], "series": []}
    colors = ("#087d82", "#e9a34c", "#5873b5")
    series = []
    for i, (label, points) in enumerate(curves):
        base = 1 + points[days[0]] / 100
        if base <= 0:
            continue
        series.append({"label": label, "color": colors[i % len(colors)],
                       "values": [round(((1 + points[day] / 100) / base - 1) * 100, 2) for day in days]})
    return {"days": days, "series": series}


def _us_return_curve(rows: list[dict], date_key: str = "date") -> dict[str, float]:
    if not rows:
        return {}
    first = float(rows[0]["equity_usd"])
    return {(str(row[date_key])[:4] + "-" + str(row[date_key])[4:6] + "-" + str(row[date_key])[6:8]
             if len(str(row[date_key])) == 8 else str(row[date_key])): (float(row["equity_usd"]) / first - 1) * 100
            for row in rows if first > 0 and row.get(date_key) and row.get("equity_usd")}


def _kr_system(accounts: list[dict] | None = None, market_day: str = "") -> dict:
    operational = latest_operational_status()
    labels = (("batch", "자동 작업"), ("telegram", "알림 발송"), ("price", "가격 데이터"))
    services = [[name, "기록 없음" if operational[key]["time"] == "기록 없음" else operational[key]["label"],
                 operational[key]["time"].replace("T", " ")[:16]] for key, name in labels]
    issues = [[row.get("source") or "—", row.get("item") or "—", row.get("status") or "—"] for row in actionable_issue_rows()]
    for account in accounts or []:
        if account.get("priceIssues"):
            services[2][1] = "확인 필요"
            issues.append(["가격 데이터", account["name"], f"종목별 점검 필요 {len(account['priceIssues'])}건"])
    # A successful old run is still stale once the saved market day has closed.
    for service in services:
        if service[1] != "정상" or not market_day:
            continue
        try:
            recorded_day = date.fromisoformat(service[2][:10])
            expected_day = date.fromisoformat(market_day)
        except ValueError:
            continue
        if recorded_day < expected_day and (expected_day < date.today() or datetime.now().hour >= 17):
            service[1] = "갱신 지연"
            issues.append([service[0], "갱신 지연", f"마지막 기록 {service[2]} · 시장 기준일 {market_day}"])
    run_labels = {"ok": "정상", "error": "오류", "failed": "오류", "not-ok": "오류", "missing": "기록 없음"}
    runs = [[display_value(row.get("step") or "—"), run_labels.get(str(row.get("status") or "").lower(), row.get("status") or "—")] for row in user_run_rows()]
    return {"services": services, "issues": issues, "runs": runs}


def _us_system(spy: list[dict], accounts: list, strategies: list[dict], market_day: str) -> dict:
    try:
        market_age = (date.today() - date.fromisoformat(market_day)).days
    except ValueError:
        market_age = STALE_DAYS + 1
    services = [["미국 시황", "정상" if market_age <= STALE_DAYS else "갱신 지연" if market_day else "기록 없음", market_day or "기록 없음"]]
    issues = []
    if market_age > STALE_DAYS:
        issues.append(["미국 시황", services[0][1], "마지막 마감 자료를 확인해 주세요."])
    if spy:
        last_day = str(spy[-1].get("date") or "")
        try:
            age = (date.today() - datetime.strptime(last_day, "%Y%m%d").date()).days
        except ValueError:
            age = STALE_DAYS + 1
        services.append(["SPY 장부", "정상" if age <= STALE_DAYS else "갱신 지연", f"{last_day[:4]}-{last_day[4:6]}-{last_day[6:8]}"])
        if age > STALE_DAYS:
            issues.append(["SPY 장부", "갱신 지연", "장부의 마지막 거래일을 확인해 주세요."])
    else:
        services.append(["SPY 장부", "기록 없음", "기록 없음"])
        issues.append(["SPY 장부", "기록 없음", "장부 파일을 확인해 주세요."])
    for account, strategy in zip(accounts, strategies):
        status = strategy["status"]
        state = str(status.get("state") or "")
        updated = str(status.get("updated_at") or "")
        try:
            age = (date.today() - datetime.fromisoformat(updated).date()).days
        except ValueError:
            age = STALE_DAYS + 1
        label = STATE_LABELS.get(state, state or "기록 없음")
        if age > STALE_DAYS and state in ("ok", "waiting"):
            label = "갱신 지연"
        services.append([account.label, label, updated.replace("T", " ")[:16] or "기록 없음"])
        if label not in ("정상", "시작 전"):
            issues.append([account.label, label, "전략 계좌의 저장 기록을 확인해 주세요."])
        if status.get("stale_holdings"):
            issues.append([account.label, "시세 없는 보유", ", ".join(map(str, status["stale_holdings"]))])
        if status.get("refresh_failures"):
            issues.append([account.label, "수집 실패", ", ".join(str(item[0]) for item in status["refresh_failures"])])
    return {"services": services, "issues": issues, "runs": []}


def _us_experiments(strategies: list[dict]) -> list[list[str]]:
    rows = []
    for strategy in strategies:
        equity = strategy["equity"]
        if not equity:
            continue
        values = [float(row["equity_usd"]) for row in equity]
        daily = [current / previous - 1 for previous, current in zip(values, values[1:]) if previous > 0]
        sharpe = f"{mean(daily) / stdev(daily) * 252 ** .5:.2f}" if len(daily) >= 20 and stdev(daily) else "기록 없음"
        last = equity[-1]
        first_day = str(equity[0].get("date") or "")
        since = f"{first_day[:4]}-{first_day[4:6]}-{first_day[6:8]}" if len(first_day) == 8 else first_day or "기록 없음"
        cash_pct = f"{float(last['cash_usd']) / values[-1] * 100:.1f}%" if values[-1] else "기록 없음"
        status = str(strategy["status"].get("state") or "")
        rows.append([strategy["account"].label, "미국 개별주 가상전략", since, _money(values[-1], "us"),
                     f"{float(last['return_pct']):.2f}" if last.get("return_pct") else "기록 없음",
                     f"{max_drawdown_pct(values):.2f}", sharpe, "기록 없음", cash_pct,
                     STATE_LABELS.get(status, status or "기록 없음")])
    return rows


def dashboard_v2_payload(market: str | None = None, view: str = "today", *, page: int = 0,
                         filter_name: str = "전체", query: str = "") -> dict:
    """Build only the market and tab requested by the read-only dashboard."""
    if market is None:
        return {name: dashboard_v2_payload(name, "today") for name in ("kr", "us")}
    if market not in {"kr", "us"} or view not in {"today", "signals", "account", "compare", "research"}:
        raise ValueError("Unknown dashboard view")
    if view == "signals":
        term = query.casefold()
        rows = [row for row in _cached_signal_history(market, _history_source_signature(market))
                if (filter_name == "전체" and (market != "kr" or row["signal"] == "매수 추천")
                    or filter_name == "매수 추천" and "매수" in row["signal"]
                    or filter_name == "매도 신호" and "매도" in row["signal"]
                    or filter_name == "추적 중" and "추적" in row["status"])
                and term in f"{row['name']} {row['ticker']} {row['reason']}".casefold()]
        return {"signalHistory": rows[page * 20:(page + 1) * 20], "signalHistoryTotal": len(rows)}

    now = datetime.now()
    if market == "kr":
        if view == "account":
            return {"accounts": [_kr_account(key, label) for key, label in (("aggressive", "적극투자형"), ("neutral", "위험중립형"))]}
        if view == "compare":
            curves = [(label, time_weighted_returns(PROFILES[key]["db_path"]))
                      for key, label in (("aggressive", "적극투자형"), ("neutral", "위험중립형"))]
            kospi = _index_cache("KOSPI")
            if kospi:
                first_close = kospi[0][1]
                curves.append(("KOSPI", {f"{day[:4]}-{day[4:6]}-{day[6:]}": (close / first_close - 1) * 100
                                         for day, close in kospi if first_close > 0}))
            experiments = [[row.get(key) or "기록 없음" for key in ("experiment", "rule", "since", "equity", "total_return_pct", "mdd_pct", "sharpe", "bear_return_pct", "cash_pct", "risk_state")] for row in experiment_account_rows()]
            return {"compareChart": _comparison_chart(curves), "experiments": experiments}
        kospi = _index_cache("KOSPI")
        latest_day = kospi[-1][0] if kospi else now.strftime("%Y%m%d")
        day = f"{latest_day[:4]}-{latest_day[4:6]}-{latest_day[6:8]}"
        if view == "research":
            from .profit_research import dashboard_data as profit_research_data
            accounts = [_kr_account(key, label, include_sales=False) for key, label in (("aggressive", "적극투자형"), ("neutral", "위험중립형"))]
            research = [[row.get("experiment", "—"), row.get("risk_state", "진행 중"), row.get("progress", "—"), now.date().isoformat(), "진행"] for row in experiment_progress_rows()]
            experiments = [[row.get(key) or "기록 없음" for key in ("experiment", "rule", "since", "equity", "total_return_pct", "mdd_pct", "sharpe", "bear_return_pct", "cash_pct", "risk_state")] for row in experiment_account_rows()]
            return {"profitResearch": profit_research_data(), "research": research, "experiments": experiments, "system": _kr_system(accounts, day)}
        picks = today_recommendation_rows()
        sells = today_sell_alert_rows()
        signals = [[r.get("name") or r.get("ticker") or "—", "매수 추천", r.get("reason") or "추천", str(r.get("created_at") or "")[-8:-3], r.get("aggressive_order_status") or "확인 중"] for r in picks]
        signals += [[r.get("name") or r.get("ticker") or "—", "매도 검토", r.get("aggressive_status") if r.get("aggressive_status") != "-" else r.get("neutral_status") or "매도 조건 충족", str(r.get("created_at") or "")[-8:-3], "확인 필요"] for r in sells]
        held_sells = home_sell_rows(sells, held_or_sold_today_tickers())
        accounts = [_kr_account(key, label, include_sales=False) for key, label in (("aggressive", "적극투자형"), ("neutral", "위험중립형"))]
        return {"day": day, "updated": now.strftime("%H:%M KST"),
                "market": [_market_entry("KOSPI", kospi), _market_entry("KOSDAQ", _index_cache("KOSDAQ"))],
                "accounts": [{key: value for key, value in account.items() if key not in {"rows", "sales"}} for account in accounts],
                "signals": signals,
                "heldSells": [[r.get("name") or r.get("ticker") or "—", r.get("aggressive_status") or "—", r.get("neutral_status") or "—"] for r in held_sells],
                "bench": "KOSPI", "chart": kospi[-252:], "signalHistoryCount": 0, "recentDecisions": []}

    strategies = [read_strategy(account) for account in ACCOUNTS]
    if view == "compare":
        curves = [(account.label, _us_return_curve(strategy["equity"])) for account, strategy in zip(ACCOUNTS, strategies)]
        spy = read_ledger()
        if spy:
            curves.append(("SPY 보유", _us_return_curve(spy)))
        return {"compareChart": _comparison_chart(curves), "experiments": _us_experiments(strategies)}
    if view == "research":
        us_market = _read_us_cache()
        day = max((str(row.get("market_date") or "") for row in us_market), default="")
        research = [[account.label, strategy["status"].get("state", "기록 대기"), "장부 갱신 확인", day or "—", "진행"] for account, strategy in zip(ACCOUNTS, strategies)]
        return {"research": research, "experiments": _us_experiments(strategies),
                "system": _us_system(read_ledger(), ACCOUNTS, strategies, day)}
    accounts = [_us_account(account, strategy) for account, strategy in zip(ACCOUNTS, strategies)]
    spy = read_ledger()
    if spy:
        last = spy[-1]
        accounts.append({"name": "SPY 보유 계좌", "kind": "비교용", "asset": _money(float(last["equity_usd"]), "us"),
                         "daily": _money(float(last["equity_usd"]) - float(spy[-2]["equity_usd"]), "us", True) if len(spy) > 1 else "기록 없음",
                         "cash": _money(float(last["cash_usd"]), "us"), "marketValue": _money(float(last["equity_usd"]) - float(last["cash_usd"]), "us"),
                         "unrealized": "기록 없음", "hold": "1종목", "returnPct": _pct(last.get("return_pct")),
                         "rows": [["SPY", "—", "—", _pct(last.get("return_pct")), "보유 중"]]})
    if view == "account":
        return {"accounts": accounts}
    us_market = _read_us_cache()
    day = max((str(row.get("market_date") or "") for row in us_market), default="")
    history = _signal_history("us", strategies)
    chart = [(row["market_date"], float(row["close"])) for row in read_csv("data/us_market_history.csv") if row.get("symbol") == ".INX" and row.get("market_date") and row.get("close")]
    return {"day": day or "기록 없음", "updated": now.strftime("%H:%M KST"),
            "market": [[row.get("name") or row.get("symbol"), f"{float(row['close']):,.2f}", _pct(row.get("change_pct"))] for row in us_market if row.get("close")],
            "accounts": [{key: value for key, value in account.items() if key != "rows"} for account in accounts],
            "signals": [], "bench": "S&P 500", "chart": chart[-252:],
            "signalHistoryCount": len(history), "recentDecisions": history[:7]}
