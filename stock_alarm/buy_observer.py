"""Capture the scheduled buy path without modifying the frozen trading engine."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager, ExitStack
from datetime import datetime
from pathlib import Path

PATH = "data/portfolio_research.db"
VERSION = "portfolio-observation-v1"
RULES = {"cap_increment_pct": 10, "relaxed_min_fill": 0.1, "fee_bps_each_side": 15,
         "horizons": [1, 3, 5, 10], "reentry_window": 10, "rotation_fraction": 0.25,
         "rotation_score_gap": 10, "reentry_wait_sessions": 3, "reentry_stop_pct": 5,
         "reentry_ma_sessions": 20, "reentry_confirm_sessions": 2}


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS buy_attempts(
        attempt_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, profile TEXT NOT NULL,
        payload TEXT NOT NULL, error TEXT);
      CREATE TABLE IF NOT EXISTS cases(
        case_id TEXT PRIMARY KEY, kind TEXT NOT NULL, profile TEXT NOT NULL,
        ticker TEXT NOT NULL, signal_day TEXT NOT NULL, payload TEXT NOT NULL,
        outcomes TEXT, complete INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at TEXT);
    """)
    for key, value in (("version", VERSION), ("rules", json.dumps(RULES, sort_keys=True)),
                       ("activated_at", datetime.now().isoformat(timespec="seconds"))):
        db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    saved = dict(db.execute("SELECT key,value FROM metadata"))
    if saved["version"] != VERSION or json.loads(saved["rules"]) != RULES:
        db.close()
        raise ValueError("Portfolio research rules changed; use a new versioned database")
    db.commit()
    return db


def allocation_plan(candidates, state, max_position, min_fill, cap_delta=0):
    """Replay only the integer-quantity budget loop, preserving candidate order."""
    cash = int(state.get("cash", 0))
    costs = {r["ticker"]: int(r["cost"]) for r in state.get("holdings", [])}
    equity = cash + sum(costs.values())
    valid = [r for r in candidates if r.get("ticker") and float(r.get("close") or 0) > 0 and r.get("price_quality", "valid") == "valid"]
    limit = min((float(r["portfolio_limit_pct"]) for r in valid), default=0)
    budget = max(0, int(equity * min(100, limit + cap_delta) / 100) - sum(costs.values()))
    spent, result = 0, []
    for row in candidates:
        ticker = row.get("ticker", "")
        item = {"ticker": ticker, "quantity": 0, "cost": 0, "portfolio_budget": budget - spent}
        price = int(float(row.get("close") or 0))
        if price <= 0 or row.get("price_quality", "valid") != "valid":
            result.append({**item, "reason": "invalid_price"})
            continue
        allocation = max(0, min(max_position, float(row.get("allocation_pct") or 0)))
        target = int(equity * allocation / 100)
        need = max(0, target - costs.get(ticker, 0))
        available = min(need, cash - spent, budget - spent)
        quantity = max(0, int(available // price))
        cost = quantity * price
        binding = "target" if need <= min(cash - spent, budget - spent) else "cash" if cash - spent <= budget - spent else "portfolio_cap"
        reason = ("allocation_zero" if allocation == 0 else "target_already_met" if need == 0
                  else "cash_empty" if cash - spent <= 0 else "portfolio_cap" if budget - spent <= 0
                  else "below_one_share" if quantity < 1 else "minimum_fill" if cost < target * min_fill else "eligible")
        item.update(target_cost=target, additional_budget=available, binding_constraint=binding,
                    min_fill_cost=target * min_fill, reason=reason)
        if reason == "eligible":
            item.update(quantity=quantity, cost=cost)
            spent += cost
        result.append(item)
    return result


@contextmanager
def intercept(module, name, observer):
    original = getattr(module, name)
    def wrapped(*args, **kwargs):
        return observer(original, *args, **kwargs)
    setattr(module, name, wrapped)
    try:
        yield
    finally:
        setattr(module, name, original)


def observe_auto_buy(original, picks, *args, **kwargs):
    from . import app, data_store, portfolio_risk
    from .trading_profiles import PROFILES
    db_path = kwargs.get("path", args[0] if args else data_store.DB_PATH)
    profile = next((key for key, value in PROFILES.items() if value["db_path"] == db_path), str(db_path))
    from .sell_check import take_profit_policy_snapshot
    payload = {"version": VERSION, "profile": profile, "source_db": str(db_path),
               "observed_at": datetime.now().isoformat(timespec="microseconds"),
               "picks": [{"ticker": p.ticker, "name": p.name, "close": p.close, "score": p.score} for p in picks],
               "auto_enabled": os.environ.get("VIRTUAL_TRADER_AUTO_BUY", "1") == "1",
               "max_position": max(0, min(100, float(os.environ.get("VIRTUAL_TRADER_MAX_POSITION_PCT", "30")))),
               "min_fill": max(0, min(1, float(os.environ.get("VIRTUAL_TRADER_MIN_FILL_RATIO", "0.5"))))}
    try:
        payload["entry_take_profit_policy"] = take_profit_policy_snapshot(PROFILES.get(profile, {}).get("sell_policy"))
        payload["entry_take_profit_policy"]["scope"] = "entry_thresholds_only_regime_permission_evaluated_at_sell_check"
    except (ValueError, TypeError) as error:
        payload["entry_take_profit_policy_error"] = type(error).__name__
    def state_capture(fn, *a, **kw):
        value = fn(*a, **kw)
        payload.setdefault("state", value)
        return value
    def risk_capture(fn, *a, **kw):
        value = fn(*a, **kw)
        payload["risk_allowed"], payload["risk_reason"] = value
        return value
    def candidate_capture(fn, candidates, *a, **kw):
        payload["candidates"] = [dict(c) for c in candidates]
        payload["quote_decision_at"] = datetime.now().astimezone().isoformat(timespec="microseconds")
        try:
            return fn(candidates, *a, **kw)
        except Exception as error:
            payload["buy_error"] = str(error)
            raise
    def capture_value(key):
        def capture(fn, *a, **kw):
            value = fn(*a, **kw)
            payload[key] = value
            return value
        return capture
    result, failure = None, None
    try:
        with ExitStack() as stack:
            for module, name, capture in (
                (data_store, "virtual_trader_state", state_capture),
                (data_store, "virtual_buy", candidate_capture),
                (portfolio_risk, "new_buys_allowed", risk_capture),
                (app, "allocation_percentages", capture_value("raw_allocations")),
                (app, "correlation_limited_allocations", capture_value("correlated_allocations")),
                (app, "sector_limited_allocations", capture_value("sector_allocations")),
                (app, "current_market_regime", capture_value("regime_used"))):
                stack.enter_context(intercept(module, name, capture))
            result = original(picks, *args, **kwargs)
        return result
    except Exception as error:
        failure = str(error)
        raise
    finally:
        try:
            payload["executions"] = (result or {}).get("executions", [])
            if payload.get("candidates"):
                try:
                    from .research_data_collect import saved_quotes
                    payload["quote_observation"] = saved_quotes([c["ticker"] for c in payload["candidates"][:10]],
                        datetime.fromisoformat(payload["quote_decision_at"]))
                except Exception as error:
                    payload["quote_observation"] = {"state":"DATA_REQUIRED","error_type":type(error).__name__}
            payload["stop_rules"] = {"floor": abs(app.env_float("SELL_LOSS_PCT", 5)),
                                     "atr_multiplier": app.env_float("SELL_ATR_MULTIPLIER", 2)}
            payload["regime_basis"] = "engine_live_label_may_fallback_sideways" if "regime_used" in payload else "unavailable"
            tickers = {p["ticker"] for p in payload["picks"]} | {h["ticker"] for h in payload.get("state", {}).get("holdings", [])}
            # Read the contemporaneous cache only; never fetch today's sectors for old cases.
            try:
                cached = json.loads(Path(".cache/sector_mapping.json").read_text(encoding="utf-8"))
                payload["sectors"] = {t: s for t, s in cached.get("mapping", {}).items() if t in tickers}
                payload["sector_as_of"] = cached.get("fetched_at")
            except (OSError, ValueError):
                payload["sectors"] = {}
            if payload.get("candidates") and payload.get("state"):
                payload["budget_plan"] = allocation_plan(payload["candidates"], payload["state"], payload["max_position"], payload["min_fill"])
            evidence = {}
            for ticker in tickers:
                rows = data_store.query_rows("""SELECT * FROM candidate_snapshots WHERE ticker=?
                    AND evaluated_at>=? AND evaluated_at<=? ORDER BY evaluated_at DESC LIMIT 1""",
                    (ticker, payload["observed_at"][:10], payload["observed_at"]), path=data_store.DB_PATH)
                if rows:
                    evidence[ticker] = rows[0]
                    scores = data_store.query_rows("SELECT profile_score FROM profile_candidate_selections WHERE run_id=? AND ticker=? AND profile=?",
                                                   (rows[0]["run_id"], ticker, profile), path=data_store.DB_PATH)
                    evidence[ticker]["profile_score_at_observation"] = scores[0]["profile_score"] if scores else None
            payload["evidence"] = evidence
            with closing(connect()) as db:
                db.execute("INSERT INTO buy_attempts VALUES (?,?,?,?,?)", (uuid.uuid4().hex, payload["observed_at"], profile, json.dumps(payload, ensure_ascii=False), failure))
                db.commit()
        except Exception as error:
            # Audit failure must not change an already executed buy or suppress its notification.
            print(f"buy observation failed: {error!r}")
            try:
                app.write_error_log(error)
            except Exception:
                pass


def main():
    from . import app
    with intercept(app, "auto_buy_virtual_trader", observe_auto_buy):
        app.main()


if __name__ == "__main__":
    main()
