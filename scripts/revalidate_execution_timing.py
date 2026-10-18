"""Paired old/new execution audit. Offline; all outputs go to a new report folder.

Usage: python scripts/revalidate_execution_timing.py {us,kr_watch,kr_all} OUTPUT
Run from the repository root. The caller supplies a consistent PIT snapshot in OUTPUT.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stock_alarm import benchmark_comparison as fixed
from stock_alarm import point_in_time_store as pit
from stock_alarm.app import load_env, configured_stocks
from stock_alarm.backtest_data import read_rows
from stock_alarm.statistical_validation import newey_west_mean_test, benjamini_hochberg
from stock_alarm.validation_backtest import BacktestEngine


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def compare(a, b):
    days = sorted(set(a) & set(b))
    values = [(a[d] - b[d]) * 100 for d in days]
    return {"days": len(days), "mean_pp": mean(values),
            "p_two_sided": newey_west_mean_test(values, 5, "two-sided")["p_value"],
            "p_greater": newey_west_mean_test(values, 5, "greater")["p_value"],
            "first_half_pp": mean((a[d] - b[d]) * 100 for d in days if d < "2022-06-01") if days[0] < "2022-06-01" else None,
            "second_half_pp": mean((a[d] - b[d]) * 100 for d in days if d >= "2022-06-01")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", choices=("us", "kr_watch", "kr_all"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    load_env()
    # No display-name lookups or external requests in a reproducibility audit.
    os.environ["KOREAN_STOCK_NAMES"] = "0"
    def no_network(*a, **kw):
        raise RuntimeError("network disabled during offline audit")
    socket.create_connection = no_network
    root = args.output.resolve()
    out = root / args.dataset
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("stock_alarm._timing_legacy", root / "legacy_benchmark_comparison.py")
    legacy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = legacy
    spec.loader.exec_module(legacy)
    weights = {k: 1.0 for k in ("volume_score", "trading_value_score", "trend_score", "relative_strength_score", "news_score", "disclosure_score", "financial_score")}
    if args.dataset == "us":
        data = ROOT / "reports/research/scripts/us_baseline_data/ohlcv"
        regimes = data.parent
        names = {p.stem: p.stem for p in sorted(data.glob("*.csv")) if p.stem != "KOSPI"}
        pit_path = out / "absent.sqlite3"
        os.environ["MIN_TRADING_VALUE"] = "5000000000"
        end = "2026-10-05"
    elif args.dataset == "kr_all":
        data = ROOT / "data/backtest_expanded/pit_universe/ohlcv"
        regimes = ROOT / "reports/research/scripts/regime_2018"
        with (data.parent / "tickers.csv").open(encoding="utf-8-sig") as f:
            names = {r["ticker"]: r["name"] for r in csv.DictReader(f) if len(read_rows(r["ticker"], data)) > 80}
        pit_path = root / "pit_snapshot.sqlite3"
        end = "2026-08-28"
    else:
        data = ROOT / "data/backtest/ohlcv"
        regimes = ROOT / "reports/backtest"
        names = configured_stocks()
        params = json.loads((regimes / "parameters.json").read_text("utf-8"))
        for k, v in params.items():
            if k.isupper() and isinstance(v, str):
                os.environ[k] = v
        pit_path = root / "pit_snapshot.sqlite3"
        end = "2026-08-28"
    # Open research snapshot read-only, reusing one connection. Queries and scores
    # are unchanged; skip schema creation/connection churn for each candidate.
    if pit_path.exists():
        import sqlite3
        connection = sqlite3.connect(pit_path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        class ReadOnlyLease:
            def execute(self, *a, **kw): return connection.execute(*a, **kw)
            def close(self): pass
        pit.connect = lambda path: ReadOnlyLease()
    engine = BacktestEngine(data_dir=data, report_dir=regimes, names=names, score_weights=weights, pit_store_path=pit_path)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (data / (t + ".csv") for t in ["KOSPI", *names])}
    configuration = {k: v for k, v in os.environ.items() if k.startswith(("SELL_", "TAKE_PROFIT_", "RISK_", "CORRELATION", "CORRELATED_", "MAX_", "MIN_", "BACKTEST_", "VOLUME_")) or k in ("TOP_N", "EXECUTION_COST_BPS")}
    dump(out / "inputs.json", {"dataset": args.dataset, "universe": names, "env": configuration, "weights": weights,
                               "data": str(data), "sha256": hashes, "pit": str(pit_path), "cutoff": end})
    sim = fixed.PortfolioSimulator(engine, 100_000_000, 10, apply_live_constraints=False)
    sim.days = [d for d in sim.days if d <= end]
    cache = {}
    start = time.monotonic()
    for i, day in enumerate(sim.days):
        cache[day] = [{"ticker": e.pick.ticker, "name": e.pick.name, "score": float(e.pick.score),
                       "volume_ratio": float(e.values.get("volume_ratio") or 0), "signal_date": day}
                      for e in engine.passed_evaluations(day) if e.pick]
        if i % 25 == 0:
            print(f"{args.dataset} candidates {i+1}/{len(sim.days)} {day} elapsed={time.monotonic()-start:.0f}s", flush=True)
    dump(out / "candidates.json", cache)
    bench = sim.buy_and_hold("benchmark", ["KOSPI"])
    benchmark_returns = fixed.daily_return_map(bench)
    # Preserve the original US report's close-to-close SPY convention as well.
    closes = engine.rows["KOSPI"]
    spy_returns = {f"{b[0][:4]}-{b[0][4:6]}-{b[0][6:]}": b[4] / a[4] - 1 for a, b in zip(closes, closes[1:])}
    variants = {"baseline": ({}, args.dataset == "kr_watch")}
    if args.dataset == "kr_all":
        variants.update({"risk_gate": ({}, True), "hold7": ({"SELL_TIME_STOP_DAYS": "7", "SELL_TIME_STOP_MIN_RETURN_PCT": "1000"}, False),
                         "hold10": ({"SELL_TIME_STOP_DAYS": "10", "SELL_TIME_STOP_MIN_RETURN_PCT": "1000"}, False)})
    results, returns = {}, {}
    for label, module in (("legacy", legacy), ("fixed", fixed)):
        results[label], returns[label] = {}, {}
        for variant, (settings, gate) in variants.items():
            saved = dict(os.environ)
            os.environ.update(settings)
            runner = module.PortfolioSimulator(engine, 100_000_000, 10, apply_live_constraints=gate)
            runner.days = sim.days
            r = runner.run("stock_alarm", cache, 20260828)
            os.environ.clear(); os.environ.update(saved)
            m = fixed.equity_metrics(r.daily_equity, r.trades, engine.regimes, "all")
            returns[label][variant] = fixed.daily_return_map(r)
            results[label][variant] = {"metrics": m, "vs_benchmark": compare(returns[label][variant], benchmark_returns)}
            if args.dataset == "us":
                results[label][variant]["vs_original_spy_convention"] = compare(returns[label][variant], spy_returns)
            dump(out / f"{label}_{variant}_equity.json", r.daily_equity)
            dump(out / f"{label}_{variant}_events.json", r.events)
            print(f"{args.dataset} {label} {variant}: {m}", flush=True)
        if args.dataset == "kr_watch":
            totals, maps = [], []
            for n in range(100):
                r = runner.run("random", cache, 20260828 + n * 100_003)
                totals.append(fixed.equity_metrics(r.daily_equity, r.trades, engine.regimes, "all")["total_return_pct"])
                maps.append(fixed.daily_return_map(r))
                if n % 10 == 0: print(f"{label} random {n+1}/100", flush=True)
            average = {d: mean(m[d] for m in maps) for d in sim.days}
            results[label]["random"] = {"median_return_pct": median(totals), "all_returns_pct": totals,
                "empirical_p": (1 + sum(t >= results[label]["baseline"]["metrics"]["total_return_pct"] for t in totals)) / 101,
                "stock_vs_mean_random": compare(returns[label]["baseline"], average)}
    paired = {v: compare(returns["fixed"][v], returns["legacy"][v]) for v in variants}
    holding = {}
    if args.dataset == "kr_all":
        for label in returns:
            holding[label] = {v: compare(returns[label][v], returns[label]["baseline"]) for v in ("hold7", "hold10")}
            qs = benjamini_hochberg([holding[label][v]["p_two_sided"] for v in ("hold7", "hold10")])
            for v,q in zip(("hold7", "hold10"), qs): holding[label][v]["q"] = q
    assert all(hashlib.sha256((data / name).read_bytes()).hexdigest() == h for name,h in hashes.items()), "source prices changed during run"
    dump(out / "results.json", {"start": sim.days[0], "end": sim.days[-1], "universe_count": len(names),
        "benchmark": fixed.equity_metrics(bench.daily_equity, bench.trades, engine.regimes, "all"),
        "results": results, "paired_fixed_minus_legacy": paired, "holding_tests": holding,
        "seconds": time.monotonic() - start})
    print(f"DONE {args.dataset}", flush=True)


if __name__ == "__main__":
    main()
