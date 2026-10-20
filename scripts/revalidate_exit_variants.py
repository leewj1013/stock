"""Pre-registered stagnation-exit / gap-open take-profit audit (reports/revalidation/2026-10-20-exit-variants/PROTOCOL.md).

Offline, read-only: reuses the frozen inputs of 2026-10-17-execution-timing and the next-open sell simulator of
revalidate_sell_timing.py. Usage (repo root): python scripts/revalidate_exit_variants.py kr_all|kr_watch
"""
import hashlib
import inspect
import json
import os
import socket
import sys
import time
from functools import lru_cache
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))
import revalidate_sell_timing as st
from revalidate_execution_timing import ROOT, BacktestEngine, dump, fixed as bc
from stock_alarm.app import env_float
from stock_alarm.statistical_validation import benjamini_hochberg, newey_west_mean_test

OUTPUT = ROOT / "reports/revalidation/2026-10-20-exit-variants"
BASE_TARGET = {"kr_all": {"total_return_pct": -46.7064, "mdd_pct": -64.1842, "sale_events": 1908}}  # 2026-10-18 sell-timing, gate off
VARIANTS = {  # name: (env override, gap mode)
    "base": ({}, ""),
    "stag3": ({"SELL_TIME_STOP_MIN_RETURN_PCT": "3"}, ""),
    "stag5": ({"SELL_TIME_STOP_MIN_RETURN_PCT": "5"}, ""),
    "gap_g1": ({}, "g1"),
    "gap_g2": ({}, "g2"),
}
HALF_SPLIT = "2022-06-01"


def gap_orders(sim, day, positions, pending_sells):
    """At the open, queue take-profit sells for held positions whose OPEN is already past the take-profit line."""
    mode = getattr(sim, "gap_mode", "")
    if not mode:
        return
    tp1, tp2 = env_float("TAKE_PROFIT_1_PCT", 10), env_float("TAKE_PROFIT_2_PCT", 20)
    ratio = env_float("TAKE_PROFIT_1_SELL_RATIO", 50) / 100
    for ticker, position in positions.items():
        item = sim.engine.by_date.get(ticker, {}).get(day)
        if ticker in pending_sells or not item or float(item[1][1]) <= 0 or float(item[1][5]) <= 0:
            continue  # same tradability test as fill_sells
        gain = (float(item[1][1]) / position["entry_price"] - 1) * 100
        sale = None
        if mode == "g2":
            sale = ("full", 1.0) if gain >= tp1 else None
        elif position["partial_taken"]:
            sale = ("full", 1.0) if gain >= tp2 else None
        elif gain >= tp2:
            sale = ("full", 1.0)  # live: first take-profit, then the second one within minutes
        elif gain >= tp1 and position["shares"] >= 2:
            sale = ("partial", ratio)
        if sale:
            pending_sells[ticker] = {"decision_date": day, "sale_type": sale[0], "quantity_fraction": sale[1],
                                     "reason": "갭 시가 익절", "stage": "gap_open"}


def next_open_run_with_gap():
    """st.next_open_run() with one extra step before each day's sell fills: queue gap-open take-profits."""
    source = inspect.getsource(st.next_open_run)
    anchor = '"            cash = fill_sells(self, day, cash, positions, pending_sells, trades, events, cooldown)\\n"'
    assert source.count(anchor) == 1
    source = source.replace(anchor, '"            gap_orders(self, day, positions, pending_sells)\\n            cash = fill_sells(self, day, cash, positions, pending_sells, trades, events, cooldown)\\n"')
    assert source.count("    namespace = dict(vars(bc), fill_sells=fill_sells)\n") == 1
    source = source.replace("    namespace = dict(vars(bc), fill_sells=fill_sells)\n", "    namespace = dict(vars(bc), fill_sells=fill_sells, gap_orders=gap_orders)\n")
    namespace = dict(vars(st), gap_orders=gap_orders)
    exec(compile(source, "<next_open_run_with_gap>", "exec"), namespace)
    return namespace["next_open_run"]()


def setup(dataset):
    source = st.SOURCE
    inputs = json.loads((source / dataset / "inputs.json").read_text("utf-8"))
    for key in list(os.environ):
        if key.startswith(("SELL_", "TAKE_PROFIT_", "RISK_", "CORRELATION", "CORRELATED_", "MAX_", "MIN_", "BACKTEST_", "VOLUME_")) or key in ("TOP_N", "EXECUTION_COST_BPS"):
            del os.environ[key]
    os.environ.update(inputs["env"])
    os.environ["KOREAN_STOCK_NAMES"] = "0"
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline audit"))
    data = Path(inputs["data"])
    for name, digest in inputs["sha256"].items():
        assert hashlib.sha256((data / name).read_bytes()).hexdigest() == digest, name
    manifest = json.loads((source / "manifest.json").read_text("utf-8"))
    assert hashlib.sha256((ROOT / "stock_alarm/benchmark_comparison.py").read_bytes()).hexdigest() == manifest["fixed_sha256"], "frozen simulator changed"
    candidates = json.loads((source / dataset / "candidates.json").read_text("utf-8"))
    regimes = ROOT / ("reports/research/scripts/regime_2018" if dataset == "kr_all" else "reports/backtest")
    engine = BacktestEngine(data_dir=data, report_dir=regimes, names=inputs["universe"], score_weights=inputs["weights"], pit_store_path=Path(inputs["pit"]))
    engine._market_up_ratio = lru_cache(maxsize=None)(engine._market_up_ratio)
    return engine, candidates, inputs


def run_variant(engine, candidates, run_fn, env, gap_mode):
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        sim = bc.PortfolioSimulator(engine, 100_000_000, 10, apply_live_constraints=False)
        sim.days = sorted(candidates)
        sim.gap_mode = gap_mode
        sim.run = run_fn.__get__(sim, type(sim))
        portfolio = sim.run("stock_alarm", candidates, 20260828, liquidate_at_end=False)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    metrics = bc.equity_metrics(portfolio.daily_equity, portfolio.trades, engine.regimes, "all")
    sales = [event for event in portfolio.events if event["event"].startswith("sell_")]
    return portfolio, metrics, sales


def audit(dataset):
    engine, candidates, _inputs = setup(dataset)
    out = OUTPUT / dataset
    out.mkdir(parents=True, exist_ok=True)
    run_fn = next_open_run_with_gap()
    results, returns = {}, {}
    for name, (env, mode) in VARIANTS.items():
        started = time.time()
        portfolio, metrics, sales = run_variant(engine, candidates, run_fn, env, mode)
        stages = {}
        for sale in sales:
            stages[sale["stage"] or sale["reason"][:12]] = stages.get(sale["stage"] or sale["reason"][:12], 0) + 1
        results[name] = {"metrics": metrics, "sale_events": len(sales), "sales_by_stage": stages,
                         "terminal_positions": portfolio.daily_state[-1]["position_count"]}
        returns[name] = bc.daily_return_map(portfolio)
        dump(out / f"{name}_equity.json", portfolio.daily_equity)
        dump(out / f"{name}_sales.json", sales)
        print(f"{dataset} {name} {time.time() - started:.0f}s total {metrics['total_return_pct']}% MDD {metrics['mdd_pct']}% sharpe {metrics['sharpe']} sales {len(sales)} {stages}", flush=True)
        if name == "base" and dataset in BASE_TARGET:  # the harness must reproduce the published baseline before anything else counts
            target = BASE_TARGET[dataset]
            got = {"total_return_pct": round(metrics["total_return_pct"], 4), "mdd_pct": round(metrics["mdd_pct"], 4), "sale_events": len(sales)}
            print("baseline reproduction:", "PASS" if got == target else f"FAIL got {got} want {target}", flush=True)
            results["baseline_reproduced"] = got == target
            if got != target:
                dump(out / "results.json", results)
                raise SystemExit("baseline not reproduced; stop")
    base = returns["base"]
    tests = []
    days = sorted(base)
    split = HALF_SPLIT if dataset == "kr_all" else days[len(days) // 2]  # kr_watch starts after HALF_SPLIT: use its own midpoint (sign check only)
    for name in ("stag3", "stag5", "gap_g1", "gap_g2"):
        diff = [(day, (returns[name][day] - base[day]) * 100) for day in days if day in returns[name]]
        values = [value for _day, value in diff]
        p = float(newey_west_mean_test(values, 5, "two-sided")["p_value"])
        first = mean(v for d, v in diff if d < split)
        second = mean(v for d, v in diff if d >= split)
        tests.append({"name": name, "days": len(values), "mean_diff_pct_per_day": mean(values), "p": p, "first_half": first, "second_half": second,
                      "mdd_delta_pp": results[name]["metrics"]["mdd_pct"] - results["base"]["metrics"]["mdd_pct"]})
    for test, q in zip(tests, benjamini_hochberg([t["p"] for t in tests])):
        test["q"] = float(q)
        test["verdict"] = ("근거 있음" if dataset == "kr_all" and test["q"] < 0.05 and test["mean_diff_pct_per_day"] > 0
                           and test["first_half"] > 0 and test["second_half"] > 0 and test["mdd_delta_pp"] >= -2.0 else "기각")
        print(f"{test['name']}: mean {test['mean_diff_pct_per_day']:+.5f}%p/day p {test['p']:.4f} q {test['q']:.4f} | halves {test['first_half']:+.5f}/{test['second_half']:+.5f} | MDD delta {test['mdd_delta_pp']:+.2f}pp -> {test['verdict']}", flush=True)
    results["tests"] = tests
    dump(out / "results.json", results)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("kr_all", "kr_watch"):
        raise SystemExit("usage: revalidate_exit_variants.py kr_all|kr_watch")
    audit(sys.argv[1])
