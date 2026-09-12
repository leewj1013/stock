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
) -> list[dict]:
    """Mirrors data_store.virtual_buy()'s per-candidate sizing loop exactly,
    kept as its own copy (not a shared refactor) so this observation-only
    module can never touch virtual_buy()'s real DB-writing code path.
    tests/test_shadow_trader.py has a parity test against virtual_buy() to
    catch the two drifting out of sync."""
    spent = 0
    orders = []
    for row in candidates:
        price = int(float(row["close"]))
        allocation_pct = max(0.0, min(max_position_pct, float(row.get("allocation_pct") or 0)))
        if not allocation_pct:
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
            continue
        if target_cost > 0 and cost < target_cost * min_fill_ratio:
            continue
        orders.append({
            "ticker": row["ticker"], "name": row.get("name", ""), "side": "BUY", "order_type": "MARKET",
            "quantity": quantity, "price": price, "cost": cost, "reason": "recommendation",
        })
        spent += cost
    return orders


def compute_shadow_buy_orders(path: str = DB_PATH) -> list[dict]:
    """What BUY orders the real account would have received today, using
    the exact tickers/prices/allocation_pct the aggressive profile's virtual
    trader ACTUALLY bought today (already sector/correlation/market-exposure
    limited -- see app.auto_buy_virtual_trader) -- just re-sized against the
    REAL account's own cash and holdings instead of the virtual account's.

    Deliberately reuses today's already-computed virtual_trades rows rather
    than re-running the recommendation pipeline: recomputing picks would
    duplicate a full universe evaluation (and its Toss stock-warning calls)
    for no benefit -- what matters for the shadow test is "would today's
    already-made decision have produced a sane real order", not re-deciding.
    """
    from .app import current_market_regime, env_float, market_exposure_limit_pct, naver_market_up_ratio
    from .dashboard import real_account_state
    from .data_store import recent_virtual_trades
    from .trading_profiles import PROFILES

    today = date.today().isoformat()
    todays_trades = [row for row in recent_virtual_trades(200, path=path) if str(row.get("created_at", "")).startswith(today)]
    if not todays_trades:
        return []
    state = real_account_state()
    if not state.get("connected") or state.get("cash", 0) <= 0:
        return []
    breadth = naver_market_up_ratio(date.today())
    market_limit = market_exposure_limit_pct(breadth)
    regime_multiplier = float(PROFILES["aggressive"]["regime_exposure_multiplier"].get(current_market_regime(date.today()), 1.0))
    exposure_limit = market_limit * regime_multiplier
    cash = int(state["cash"])
    open_costs = {
        str(row.get("ticker") or ""): int(float(row.get("average_price") or 0)) * int(float(row.get("quantity") or 0))
        for row in state.get("holdings", [])
    }
    account_equity = int(state["total_equity"])
    portfolio_budget = max(0, int(account_equity * exposure_limit / 100) - sum(open_costs.values()))
    min_fill_ratio = max(0.0, min(1.0, env_float("VIRTUAL_TRADER_MIN_FILL_RATIO", 0.5)))
    max_position_pct = max(0.0, min(100.0, env_float("VIRTUAL_TRADER_MAX_POSITION_PCT", 30)))
    candidates = [
        {"ticker": row["ticker"], "name": row.get("name", ""), "close": row["price"], "allocation_pct": row["allocation_pct"]}
        for row in todays_trades
    ]
    return _size_buy_orders(candidates, cash, open_costs, account_equity, max_position_pct, min_fill_ratio, portfolio_budget)


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
    """Daily entry point: compute today's would-be real-account buy/sell
    orders and log them to shadow_orders. Read-only against Toss end to end
    -- no order is ever placed."""
    from .app import load_env
    load_env()
    buy_orders = compute_shadow_buy_orders(path=path)
    sell_orders = compute_shadow_sell_orders()
    recorded = record_shadow_orders(buy_orders + sell_orders, path=path)
    return {"buy_orders": len(buy_orders), "sell_orders": len(sell_orders), "recorded": recorded}


def main() -> None:
    result = run()
    print(f"shadow_trader buy_orders={result['buy_orders']} sell_orders={result['sell_orders']} recorded={result['recorded']}")


if __name__ == "__main__":
    main()
