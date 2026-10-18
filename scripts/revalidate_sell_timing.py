"""Offline paired audit of Korean close-signal sell execution at the next tradable open."""
import hashlib
import inspect
import json
import os
from functools import lru_cache
from pathlib import Path
import socket
import sys
import textwrap

from revalidate_execution_timing import ROOT, BacktestEngine, dump, fixed as bc

SOURCE = ROOT / "reports/revalidation/2026-10-17-execution-timing"
OUTPUT = ROOT / "reports/revalidation/2026-10-18-sell-timing"


def next_open_run(buy_policy="existing"):
    """Reuse the frozen simulator with next-open sells and optional research buy filters."""
    assert buy_policy in ("existing", "positive_volume", "prior_volume_1pct")
    source = inspect.getsource(bc.PortfolioSimulator.run)
    if buy_policy != "existing":
        anchor = '                if not item or float(item[1][1]) <= 0 or len(positions) >= position_limit:\n'
        assert source.count(anchor) == 1
        source = source.replace(anchor, '                if not item or float(item[1][1]) <= 0 or float(item[1][5]) <= 0 or len(positions) >= position_limit:\n')
    if buy_policy == "prior_volume_1pct":
        anchor = '                shares = int(budget // open_price)\n'
        assert source.count(anchor) == 1
        source = source.replace(anchor, '''                shares = int(budget // open_price)
                previous_item = self.engine.by_date.get(ticker, {}).get(self.days[day_index - 1]) if day_index else None
                shares = min(shares, int(float(previous_item[1][5]) * 0.01)) if previous_item else 0
''')
    # shortcut: fixed per-side bps ignores auction depth; replace when opening-order quotes are available.
    anchor = '                open_price = float(item[1][1])\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, '                open_price = float(item[1][1]) * (1 + getattr(self, "extra_slippage_bps", 0) / 10000)\n')
    source = source.replace(
        "        pending: dict[str, dict] = {}\n",
        "        pending: dict[str, dict] = {}\n        pending_sells: dict[str, dict] = {}\n",
        1,
    )
    anchor = "            remaining_pending = len(pending)\n"
    assert source.count(anchor) == 1
    source = source.replace(anchor, "            cash = fill_sells(self, day, cash, positions, pending_sells, trades, events, cooldown)\n" + anchor)
    anchor = "            for ticker, position in list(positions.items()):\n                history = self.engine._history(ticker, day)\n"
    assert source.count(anchor) == 1
    source = source.replace(anchor, "            for ticker, position in list(positions.items()):\n                if ticker in pending_sells:\n                    continue\n                history = self.engine._history(ticker, day)\n")
    start = source.index('                if alert.sale_type == "partial" and position["shares"] > 1:\n')
    end = source.index("                cooldown[ticker] = day\n", start) + len("                cooldown[ticker] = day\n")
    source = source[:start] + '''                pending_sells[ticker] = {
                    "decision_date": day, "sale_type": alert.sale_type,
                    "quantity_fraction": alert.quantity_fraction,
                    "reason": alert.reason, "stage": alert.stage,
                }
''' + source[end:]
    anchor = "        return PortfolioResult(strategy, equity_rows, trades, daily_state, events)\n"
    assert source.count(anchor) == 1
    source = source.replace(anchor, "        self.pending_sell_orders = dict(pending_sells)\n" + anchor)
    source = source.replace("        cash = self.initial_cash\n", "        assert not liquidate_at_end, 'Research audit leaves terminal positions marked'\n        cash = self.initial_cash\n", 1)
    namespace = dict(vars(bc), fill_sells=fill_sells)
    exec(compile(textwrap.dedent(source), str(OUTPUT / "generated_run.py"), "exec"), namespace)
    return namespace["run"]


def fill_sells(sim, day, cash, positions, orders, trades, events, cooldown):
    for ticker, order in list(orders.items()):
        item = sim.engine.by_date.get(ticker, {}).get(day)
        if not item or float(item[1][1]) <= 0 or float(item[1][5]) <= 0:
            continue
        position = positions[ticker]
        price = float(item[1][1]) * (1 - getattr(sim, "extra_slippage_bps", 0) / 10000)
        partial = order["sale_type"] == "partial" and position["shares"] > 1
        sold = min(max(1, int(position["shares"] * order["quantity_fraction"])), position["shares"] - 1) if partial else position["shares"]
        equity_before = cash + sum(p["shares"] * sim._open_mark(p["ticker"], day, p["entry_price"]) for p in positions.values())
        cost = position["basis"] * sold / position["initial_shares"] * sim.cost_rate
        cash += sim._sale_proceeds(position, sold, price)
        events.append({
            "date": day, "decision_date": order["decision_date"], "event": "sell_partial" if partial else "sell_full",
            "ticker": ticker, "name": position["name"], "signal_date": position["signal_date"],
            "entry_date": position["entry_date"], "entry_price": position["entry_price"],
            "initial_shares": position["initial_shares"], "sale_fraction_initial": sold / position["initial_shares"],
            "entry_equity": position["entry_equity"], "shares": sold, "price": price,
            "gross_notional": sold * price, "transaction_cost": cost, "reason": order["reason"],
            "stage": order["stage"], "portfolio_equity_before": equity_before,
        })
        orders.pop(ticker)
        if partial:
            position["shares"] -= sold
            position["partial_taken"] = True
            position["first_take_profit_date"] = day
            position["first_take_profit_price"] = int(price)
            continue
        total_return = (position["net_sale_proceeds"] / position["basis"] - 1) * 100
        benchmark = sim.engine.by_date.get("KOSPI", {})
        entry, exit_item = benchmark.get(position["entry_date"]), benchmark.get(day)
        opening = float(entry[1][1]) if entry else 0.0
        benchmark_return = (float(exit_item[1][1]) / opening - 1) * 100 if opening and exit_item else 0.0
        trades.append(bc.Trade(
            ticker, position["name"], position["signal_date"], position["entry_date"], day,
            int(position["entry_price"]), int(price), round(total_return, 4), round(benchmark_return, 4),
            round(total_return - benchmark_return, 4), sim.engine.regimes.get(position["signal_date"], "unclassified"),
            order["reason"], True, position["first_take_profit_date"], position["first_take_profit_price"],
        ))
        positions.pop(ticker)
        cooldown[ticker] = day
    return cash


def audit(dataset):
    inputs = json.loads((SOURCE / dataset / "inputs.json").read_text("utf-8"))
    for key in list(os.environ):
        if key.startswith(("SELL_", "TAKE_PROFIT_", "RISK_", "CORRELATION", "CORRELATED_", "MAX_", "MIN_", "BACKTEST_", "VOLUME_")) or key in ("TOP_N", "EXECUTION_COST_BPS"):
            del os.environ[key]
    os.environ.update(inputs["env"])
    os.environ["KOREAN_STOCK_NAMES"] = "0"
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline audit"))
    data = Path(inputs["data"])
    for name, digest in inputs["sha256"].items():
        assert hashlib.sha256((data / name).read_bytes()).hexdigest() == digest, name
    manifest = json.loads((SOURCE / "manifest.json").read_text("utf-8"))
    assert hashlib.sha256((ROOT / "stock_alarm/benchmark_comparison.py").read_bytes()).hexdigest() == manifest["fixed_sha256"]
    candidates_path = SOURCE / dataset / "candidates.json"
    candidates = json.loads(candidates_path.read_text("utf-8"))
    regimes = ROOT / ("reports/research/scripts/regime_2018" if dataset == "kr_all" else "reports/backtest")
    engine = BacktestEngine(data_dir=data, report_dir=regimes, names=inputs["universe"], score_weights=inputs["weights"], pit_store_path=Path(inputs["pit"]))
    engine._market_up_ratio = lru_cache(maxsize=None)(engine._market_up_ratio)
    out = OUTPUT / dataset
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "inputs.json", {"source": str(SOURCE / dataset), "candidate_sha256": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                               "simulator_sha256": manifest["fixed_sha256"], "last_day": max(candidates)})
    result = {}
    for gate in ([False, True] if dataset == "kr_all" else [True, False]):
        label = "gate_on" if gate else "gate_off"
        runs = {}
        for execution in ("same_close", "next_open"):
            sim = bc.PortfolioSimulator(engine, 100_000_000, 10, apply_live_constraints=gate)
            sim.days = sorted(candidates)
            if execution == "next_open":
                sim.run = next_open_run().__get__(sim, type(sim))
            portfolio = sim.run("stock_alarm", candidates, 20260828, liquidate_at_end=False)
            metrics = bc.equity_metrics(portfolio.daily_equity, portfolio.trades, engine.regimes, "all")
            sales = [event for event in portfolio.events if event["event"].startswith("sell_")]
            runs[execution] = {"metrics": metrics, "sale_events": len(sales),
                               "terminal_positions": portfolio.daily_state[-1]["position_count"],
                               "terminal_pending_sells": len(getattr(sim, "pending_sell_orders", {})),
                               "terminal_pending_sell_orders": getattr(sim, "pending_sell_orders", {}),
                               "delayed_sells": sum(event["date"] != sim.days[sim.days.index(event["decision_date"]) + 1]
                                                    for event in sales if event.get("decision_date") and sim.days.index(event["decision_date"]) + 1 < len(sim.days))}
            dump(out / f"{label}_{execution}_equity.json", portfolio.daily_equity)
            dump(out / f"{label}_{execution}_sales.json", sales)
            print(dataset, label, execution, runs[execution], flush=True)
        result[label] = {"runs": runs, "return_delta_pp": round(runs["next_open"]["metrics"]["total_return_pct"] - runs["same_close"]["metrics"]["total_return_pct"], 4)}
    dump(out / "results.json", result)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("kr_all", "kr_watch"):
        raise SystemExit("usage: revalidate_sell_timing.py kr_all|kr_watch")
    audit(sys.argv[1])
