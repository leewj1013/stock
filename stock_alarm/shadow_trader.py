from __future__ import annotations

from contextlib import closing
from datetime import date, datetime

from .data_store import DB_PATH, connect


def _size_buy_orders(
    candidates: list[dict],
    cash: int,
    open_costs: dict[str, int],
    account_equity: int,
    max_position_pct: float,
    min_fill_ratio: float,
    portfolio_budget: int,
    decisions: list[dict] | None = None,
) -> list[dict]:
    """Mirrors data_store.virtual_buy()'s per-candidate sizing loop exactly,
    kept as its own copy (not a shared refactor) so this observation-only
    module can never touch virtual_buy()'s real DB-writing code path.
    tests/test_shadow_trader.py has a parity test against virtual_buy() to
    catch the two drifting out of sync. `decisions`, when given, receives one
    entry per candidate saying why it was ordered or skipped."""
    spent = 0
    orders = []

    def note(row: dict, target: int, cost: int, verdict: str) -> None:
        if decisions is not None:
            decisions.append({"ticker": row["ticker"], "name": row.get("name", ""), "target_cost": target,
                              "cost": cost, "verdict": verdict})

    for row in candidates:
        price = int(float(row["close"]))
        allocation_pct = max(0.0, min(max_position_pct, float(row.get("allocation_pct") or 0)))
        if not allocation_pct:
            note(row, 0, 0, "비중 0% (상관관계·업종 한도)")
            continue
        target_cost = int(account_equity * allocation_pct / 100)
        additional_budget = min(
            max(0, target_cost - open_costs.get(row["ticker"], 0)),
            cash - spent,
            portfolio_budget - spent,
        )
        quantity = int(additional_budget // price)
        cost = price * quantity
        if quantity < 1 or spent + cost > cash:
            note(row, target_cost, cost, "예산 부족")
            continue
        if target_cost > 0 and cost < target_cost * min_fill_ratio:
            note(row, target_cost, cost, f"남은 예산이 목표의 {min_fill_ratio:.0%} 미만")
            continue
        orders.append({
            "ticker": row["ticker"], "name": row.get("name", ""), "side": "BUY", "order_type": "MARKET",
            "quantity": quantity, "price": price, "cost": cost, "reason": "recommendation",
        })
        note(row, target_cost, cost, "주문")
        spent += cost
    return orders


DECISIONS_LOG = "logs/shadow_decisions.csv"


def _shadow_buys_today(path: str) -> dict[str, int]:
    """Cost already committed to shadow BUY orders today, per ticker.

    Buys are now sized at the moment each virtual purchase happens, so a later
    run the same day must treat the earlier shadow buys as held -- otherwise
    every intraday cycle would spend the full budget again.
    """
    from .data_store import query_rows

    spent: dict[str, int] = {}
    for row in query_rows(
        "SELECT ticker, cost FROM shadow_orders WHERE side='BUY' AND created_at LIKE ?",
        (f"{date.today().isoformat()}%",), path,
    ):
        spent[str(row["ticker"])] = spent.get(str(row["ticker"]), 0) + int(row["cost"] or 0)
    return spent


def shadow_ledger(path: str = DB_PATH, until: str | None = None, capital: int | None = None) -> dict:
    """The hypothetical real account implied by shadow_orders (up to `until`).

    Cash starts from SHADOW_TRADER_ASSUMED_CAPITAL; buys add at cost, sells
    remove shares at their average cost and book the difference as realized.
    """
    from .app import env_float
    from .data_store import query_rows

    capital = int(env_float("SHADOW_TRADER_ASSUMED_CAPITAL", 0)) if capital is None else capital
    rows = query_rows("SELECT * FROM shadow_orders ORDER BY created_at, shadow_order_id", path=path)
    held: dict[str, dict] = {}
    cash, realized = capital, 0
    for row in rows:
        if until and str(row["created_at"]) > until:
            break
        ticker, quantity, price = str(row["ticker"]), int(row["quantity"] or 0), int(row["price"] or 0)
        position = held.setdefault(ticker, {"ticker": ticker, "name": row.get("name") or ticker, "quantity": 0, "cost": 0})
        if row["side"] == "BUY":
            position["quantity"] += quantity
            position["cost"] += int(row["cost"] or price * quantity)
            cash -= int(row["cost"] or price * quantity)
        elif row["side"] == "SELL" and position["quantity"]:
            quantity = min(quantity, position["quantity"])
            cost_out = round(position["cost"] * quantity / position["quantity"])
            realized += price * quantity - cost_out
            cash += price * quantity
            position["quantity"] -= quantity
            position["cost"] -= cost_out
    holdings = [position for position in held.values() if position["quantity"] > 0]
    return {"capital": capital, "cash": cash, "realized": realized, "holdings": holdings}


def shadow_portfolio(path: str = DB_PATH) -> dict:
    """Shadow ledger valued at the latest close, next to the virtual account
    over the same stretch (from the first shadow order), or {} if none."""
    from datetime import timedelta

    from .app import naver_rows
    from .dashboard import time_weighted_returns
    from .data_store import query_rows

    first = query_rows("SELECT MIN(created_at) AS first FROM shadow_orders", path=path)
    if not first or not first[0]["first"]:
        return {}
    since = str(first[0]["first"])[:10]
    ledger = shadow_ledger(path)
    if ledger["capital"] <= 0:
        return {}  # no assumed capital: nothing to measure a return against
    holdings = []
    for row in ledger["holdings"]:
        try:
            rows = naver_rows(row["ticker"], date.today() - timedelta(days=10), date.today(), max_cache_age_seconds=300)
            price = int(rows[-1][4]) if rows else 0
        except Exception:
            price = 0
        value = price * row["quantity"] if price else row["cost"]
        holdings.append({**row, "average_price": round(row["cost"] / row["quantity"]), "current_price": price or "",
                         "valuation": value, "profit_loss": value - row["cost"],
                         "return_pct": f"{(value / row['cost'] - 1) * 100:.2f}" if row["cost"] else ""})
    equity = ledger["cash"] + sum(row["valuation"] for row in holdings)
    curve = time_weighted_returns(path)
    before = [value for day, value in curve.items() if day < since]
    virtual = ((1 + list(curve.values())[-1] / 100) / (1 + (before[-1] if before else 0) / 100) - 1) * 100 if curve else None
    return {
        "since": since, "capital": ledger["capital"], "cash": ledger["cash"], "equity": equity,
        "realized": ledger["realized"], "unrealized": sum(row["profit_loss"] for row in holdings),
        "return_pct": (equity / ledger["capital"] - 1) * 100 if ledger["capital"] else None,
        "virtual_return_pct": virtual, "holdings": holdings,
    }


MIRROR_TAG = "virtual sale_id="


def sync_shadow_sells(path: str = DB_PATH, now: datetime | None = None) -> int:
    """Mirror every aggressive virtual sale onto the shadow position it maps to.

    Sells the same fraction of the shadow holding that the virtual account
    sold of its own (all of it on a full exit), at the virtual sale price and
    time. Idempotent: each mirror carries its sale_id, so the intraday hook and
    a one-off backfill can share this. Sales older than a few minutes when
    first mirrored are tagged as a backfill.
    """
    from .data_store import query_rows

    orders = query_rows("SELECT created_at, reason FROM shadow_orders ORDER BY created_at", path=path)
    if not orders:
        return 0
    first = str(orders[0]["created_at"])
    mirrored = {str(row["reason"]).split(MIRROR_TAG, 1)[1].split()[0] for row in orders if MIRROR_TAG in str(row["reason"] or "")}
    sales = query_rows("SELECT * FROM virtual_sales WHERE created_at >= ? ORDER BY sale_id", (first,), path)
    now = now or datetime.now()
    recorded = 0
    for sale in sales:
        if str(sale["sale_id"]) in mirrored:
            continue
        ticker, at = str(sale["ticker"]), str(sale["created_at"])
        bought = query_rows("SELECT COALESCE(SUM(quantity), 0) AS q FROM virtual_trades WHERE ticker=? AND created_at <= ?", (ticker, at), path)
        sold_before = query_rows("SELECT COALESCE(SUM(quantity), 0) AS q FROM virtual_sales WHERE ticker=? AND sale_id < ?", (ticker, sale["sale_id"]), path)
        virtual_before = int(bought[0]["q"]) - int(sold_before[0]["q"])
        shadow = next((row for row in shadow_ledger(path, until=at)["holdings"] if row["ticker"] == ticker), None)
        if not shadow or virtual_before <= 0:
            continue
        fraction = min(1.0, int(sale["quantity"]) / virtual_before)
        quantity = shadow["quantity"] if sale["sale_type"] == "full" or fraction >= 0.999 else int(shadow["quantity"] * fraction)
        if quantity < 1:
            continue
        backfill = "[사후 보정] " if (now - datetime.fromisoformat(at)).total_seconds() > 600 else ""
        from .sell_check import short_reason
        with closing(connect(path)) as connection:
            connection.execute(
                """INSERT INTO shadow_orders(created_at, ticker, name, side, order_type, quantity, price, cost, reason)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (at, ticker, sale["name"], "SELL", "MARKET", quantity, int(sale["price"]), int(sale["price"]) * quantity,
                 f"{backfill}{short_reason(sale['reason'])} ({MIRROR_TAG}{sale['sale_id']} )"),
            )
            connection.commit()
        recorded += 1
    return recorded


def plan_shadow_buys(trades: list[dict], path: str = DB_PATH) -> tuple[list[dict], list[dict], dict]:
    """(orders, per-candidate decisions, context) for the real account.

    Re-sizes exactly the tickers/prices/allocation_pct the aggressive virtual
    trader just bought -- in the order it bought them, so a short budget
    keeps the same names the virtual account kept -- against the REAL
    account's cash and holdings, with the market limit read at that same
    moment. `context` records the limit and budget so a skipped order can be
    explained later without replaying the day.
    """
    from .app import current_market_regime, env_float, market_exposure_limit_pct, naver_market_up_ratio
    from .dashboard import real_account_state
    from .trading_profiles import PROFILES

    trades = sorted(trades, key=lambda row: (int(row.get("trade_id") or 0), str(row.get("created_at") or "")))
    if not trades:
        return [], [], {}
    state = real_account_state()
    # Before any real deposit the account's cash sizes every order to zero;
    # this lets the sizing logic be reviewed against a hypothetical balance
    # instead. Real holdings still constrain it (open costs, correlation/sector).
    assumed_capital = int(env_float("SHADOW_TRADER_ASSUMED_CAPITAL", 0))
    if assumed_capital <= 0 and (not state.get("connected") or state.get("cash", 0) <= 0):
        return [], [], {"skipped": "no_capital"}
    breadth = naver_market_up_ratio(date.today())
    market_limit = market_exposure_limit_pct(breadth)
    regime = current_market_regime(date.today())
    regime_multiplier = float(PROFILES["aggressive"]["regime_exposure_multiplier"].get(regime, 1.0))
    exposure_limit = market_limit * regime_multiplier
    open_costs = {
        str(row.get("ticker") or ""): int(float(row.get("average_price") or 0)) * int(float(row.get("quantity") or 0))
        for row in state.get("holdings", [])
    }
    earlier_today = _shadow_buys_today(path)
    for ticker, cost in earlier_today.items():
        open_costs[ticker] = open_costs.get(ticker, 0) + cost
    spent_today = sum(earlier_today.values())
    if assumed_capital > 0:
        # No real money yet: the shadow account's own earlier buys are its
        # holdings, not just today's -- otherwise every day spends the full
        # assumed capital again.
        ledger = shadow_ledger(path, capital=assumed_capital)
        open_costs = {row["ticker"]: row["cost"] for row in ledger["holdings"]}
        account_equity = assumed_capital + ledger["realized"]
        cash = ledger["cash"]
    else:
        account_equity = int(state["total_equity"])
        cash = int(state["cash"]) - spent_today
    cash = max(0, cash)
    portfolio_budget = max(0, int(account_equity * exposure_limit / 100) - sum(open_costs.values()))
    min_fill_ratio = max(0.0, min(1.0, env_float("VIRTUAL_TRADER_MIN_FILL_RATIO", 0.5)))
    max_position_pct = max(0.0, min(100.0, env_float("VIRTUAL_TRADER_MAX_POSITION_PCT", 30)))
    candidates = [
        {"ticker": row["ticker"], "name": row.get("name", ""), "close": row["price"], "allocation_pct": row["allocation_pct"]}
        for row in trades
    ]
    if state.get("holdings"):
        from .app import Pick, correlation_limited_allocations, sector_limited_allocations
        candidate_tickers = {str(row["ticker"]) for row in candidates}
        existing_rows = [row for row in state["holdings"] if str(row.get("ticker") or "") not in candidate_tickers]
        existing = [
            Pick(
                str(row["ticker"]), str(row.get("name") or row["ticker"]),
                int(float(row.get("current_price") or row.get("average_price") or 0)), 0, 0, 0,
            )
            for row in existing_rows
        ]
        proposed = [Pick(str(row["ticker"]), str(row.get("name") or row["ticker"]), int(row["close"]), 0, 0, 0) for row in candidates]
        existing_allocations = [float(row.get("valuation") or 0) / account_equity * 100 for row in existing_rows]
        allocations = existing_allocations + [float(row["allocation_pct"]) for row in candidates]
        locked = {pick.ticker for pick in existing}
        allocations = correlation_limited_allocations(existing + proposed, allocations, locked_tickers=locked)
        sector_cap = env_float("SECTOR_GROUP_MAX_PCT", 100)
        if sector_cap < 100:
            allocations = sector_limited_allocations(
                existing + proposed, allocations, locked_tickers=locked, group_cap_override=sector_cap,
            )
        for row, allocation in zip(candidates, allocations[len(existing):]):
            row["allocation_pct"] = allocation
    decisions: list[dict] = []
    orders = _size_buy_orders(
        candidates, cash, open_costs, account_equity, max_position_pct, min_fill_ratio, portfolio_budget, decisions,
    )
    context = {
        "breadth": round(float(breadth), 4), "regime": regime, "market_limit_pct": market_limit,
        "exposure_limit_pct": exposure_limit, "account_equity": account_equity, "budget": portfolio_budget,
        "spent_earlier_today": spent_today, "assumed_capital": assumed_capital,
    }
    capital_note = f"가정금액 {assumed_capital:,}원 · " if assumed_capital > 0 else ""
    for order in orders:
        order["reason"] = (f"recommendation ({capital_note}보유한도 {exposure_limit:g}% · "
                           f"남은예산 {portfolio_budget:,}원)")
    return orders, decisions, context


def log_decisions(decisions: list[dict], context: dict, path: str = DECISIONS_LOG) -> None:
    """Append one row per candidate, including the ones that were skipped."""
    import csv
    import os

    if not decisions:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fields = ["logged_at", "ticker", "name", "verdict", "target_cost", "cost", "breadth", "regime",
              "exposure_limit_pct", "budget", "spent_earlier_today", "assumed_capital"]
    new_file = not os.path.exists(path)
    now = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for decision in decisions:
            writer.writerow({"logged_at": now, **context, **decision})


def compute_shadow_buy_orders(path: str = DB_PATH) -> list[dict]:
    """All of today's aggressive virtual buys re-sized in one pass (kept for
    ad-hoc review; the scheduled path is record_intraday_buys)."""
    from .data_store import query_rows

    trades = query_rows(
        "SELECT * FROM virtual_trades WHERE created_at LIKE ? ORDER BY trade_id",
        (f"{date.today().isoformat()}%",), path,
    )
    return plan_shadow_buys(trades, path)[0]


def latest_trade_id(path: str = DB_PATH) -> int:
    from .data_store import query_rows

    rows = query_rows("SELECT COALESCE(MAX(trade_id), 0) AS latest FROM virtual_trades", path=path)
    return int(rows[0]["latest"]) if rows else 0


def record_intraday_buys(after_trade_id: int, path: str = DB_PATH) -> dict:
    """Shadow the aggressive account's buys made since `after_trade_id`.

    Called right after each intraday virtual buy, so the real-account order
    is sized with the same market reading the virtual order used -- a single
    16:15 pass re-cut the whole day with the closing reading instead, and
    dropped most of a day's buys whenever the market mode changed.
    """
    from .data_store import query_rows

    trades = query_rows(
        "SELECT * FROM virtual_trades WHERE trade_id > ? ORDER BY trade_id", (after_trade_id,), path,
    )
    if not trades:
        return {"trades": 0, "recorded": 0}
    orders, decisions, context = plan_shadow_buys(trades, path)
    log_decisions(decisions, context)
    recorded = record_shadow_orders(orders, path=path)
    print(f"shadow_trader intraday trades={len(trades)} orders={len(orders)} "
          f"limit={context.get('exposure_limit_pct')}% budget={context.get('budget')}")
    return {"trades": len(trades), "recorded": recorded, "context": context}


def compute_shadow_sell_orders() -> list[dict]:
    """What SELL orders the real account would receive today: a full exit
    for any domestic holding under an active blocking stock warning
    (liquidation trading / investment risk) -- the same signal sell_check.py
    already force-sells on for tracked positions. Never calls Toss's order
    API. Uses the held quantity directly rather than a live
    sellable-quantity check (a pending sell would be the only source of
    drift, and is rare/short-lived enough not to be worth another Toss call
    in this observation-only pass)."""
    from .dashboard import real_account_state
    state = real_account_state()
    if not state.get("connected"):
        return []
    orders = []
    for holding in state.get("holdings", []):
        if not str(holding.get("watch_state", "")).startswith("종목 경고"):
            continue
        quantity = int(float(holding.get("quantity") or 0))
        if quantity < 1:
            continue
        orders.append({
            "ticker": str(holding.get("ticker") or ""), "name": str(holding.get("name") or ""),
            "side": "SELL", "order_type": "MARKET", "quantity": quantity,
            "price": int(float(holding.get("current_price") or 0)), "cost": 0,
            "reason": holding.get("watch_state", ""),
        })
    return orders


def record_shadow_orders(orders: list[dict], path: str = DB_PATH) -> int:
    if not orders:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        connection.executemany(
            """INSERT INTO shadow_orders(created_at, ticker, name, side, order_type, quantity, price, cost, reason)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (now, order["ticker"], order.get("name", ""), order["side"], order["order_type"],
                 order["quantity"], order.get("price", 0), order.get("cost", 0), order.get("reason", ""))
                for order in orders
            ],
        )
        connection.commit()
    return len(orders)


def run(path: str = DB_PATH) -> dict:
    """After-close entry point: log the SELL orders the real account would
    receive. BUY orders are recorded intraday by record_intraday_buys at the
    moment each virtual buy happens. Read-only against Toss -- no order is
    ever placed."""
    from .app import load_env
    load_env()
    sell_orders = compute_shadow_sell_orders()
    recorded = record_shadow_orders(sell_orders, path=path)
    return {"buy_orders": 0, "sell_orders": len(sell_orders), "recorded": recorded}


def main() -> None:
    result = run()
    print(f"shadow_trader buy_orders={result['buy_orders']} sell_orders={result['sell_orders']} recorded={result['recorded']}")


if __name__ == "__main__":
    main()
