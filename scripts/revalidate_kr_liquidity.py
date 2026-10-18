"""Offline buy-fill and stale-holding sensitivity audit on the frozen Korean snapshot."""
import hashlib
import json
import os
from collections import Counter
from functools import lru_cache
from pathlib import Path
import socket
import sys

from revalidate_execution_timing import ROOT, BacktestEngine, dump, fixed as bc
from revalidate_sell_timing import SOURCE, OUTPUT as SELL_OUTPUT, next_open_run

OUTPUT = ROOT / "reports/revalidation/2026-10-18-kr-liquidity"


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
    previous_audit = json.loads((SELL_OUTPUT / dataset / "results.json").read_text("utf-8"))
    regimes = ROOT / ("reports/research/scripts/regime_2018" if dataset == "kr_all" else "reports/backtest")
    engine = BacktestEngine(data_dir=data, report_dir=regimes, names=inputs["universe"], score_weights=inputs["weights"], pit_store_path=Path(inputs["pit"]))
    engine._market_up_ratio = lru_cache(maxsize=None)(engine._market_up_ratio)
    days = sorted(candidates)
    previous = {day: days[i - 1] if i else None for i, day in enumerate(days)}
    out = OUTPUT / dataset
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "inputs.json", {"source": str(SOURCE / dataset), "candidate_sha256": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                               "simulator_sha256": manifest["fixed_sha256"], "last_day": days[-1]})
    results = {}
    for gate in ([False, True] if dataset == "kr_all" else [True, False]):
        label = "gate_on" if gate else "gate_off"
        results[label] = {}
        for policy in ("existing", "positive_volume", "prior_volume_1pct"):
            sim = bc.PortfolioSimulator(engine, 100_000_000, 10, apply_live_constraints=gate)
            sim.days = days
            sim.run = next_open_run(policy).__get__(sim, type(sim))
            portfolio = sim.run("stock_alarm", candidates, 20260828, liquidate_at_end=False)
            metrics = bc.equity_metrics(portfolio.daily_equity, portfolio.trades, engine.regimes, "all")
            if policy == "existing":
                assert metrics == previous_audit[label]["runs"]["next_open"]["metrics"], "sell timing baseline changed"
            buys = [event for event in portfolio.events if event["event"] == "buy"]
            impossible, over_cap, absent_previous = [], [], 0
            for event in buys:
                item = engine.by_date[event["ticker"]].get(event["date"])
                prior = engine.by_date[event["ticker"]].get(previous[event["date"]]) if previous[event["date"]] else None
                if not item or float(item[1][1]) <= 0 or float(item[1][5]) <= 0:
                    impossible.append(event)
                if not prior:
                    absent_previous += 1
                elif event["shares"] > int(float(prior[1][5]) * .01):
                    over_cap.append(event)
            if policy == "positive_volume":
                assert not impossible
            if policy == "prior_volume_1pct":
                assert not impossible and not over_cap and absent_previous == 0
            stale_days = stale_peak_pct = stale_missing = stale_zero_volume = 0
            stale_tickers = Counter()
            stale_marks = []
            for state in portfolio.daily_state:
                values = json.loads(state["position_values_json"])
                stale_value = 0
                for ticker, value in values.items():
                    item = engine.by_date[ticker].get(state["date"])
                    if not item or float(item[1][5]) <= 0:
                        stale_value += value
                        stale_tickers[ticker] += 1
                        stale_missing += not bool(item)
                        stale_zero_volume += bool(item)
                stale_marks.append(stale_value)
                stale_days += stale_value > 0
                stale_peak_pct = max(stale_peak_pct, stale_value / state["equity"] * 100 if state["equity"] else 0)
            last = portfolio.daily_state[-1]
            last_values = json.loads(last["position_values_json"])
            stale_last = {ticker: value for ticker, value in last_values.items()
                          if not (item := engine.by_date[ticker].get(days[-1])) or float(item[1][5]) <= 0}
            final_equity = portfolio.daily_equity[-1]["equity"]
            stressed_mdd = {}
            for recovery in (.5, 0):
                marked = [{**row, "equity": row["equity"] - (1 - recovery) * stale}
                          for row, stale in zip(portfolio.daily_equity, stale_marks)]
                stressed_mdd[str(recovery)] = bc.equity_metrics(marked, portfolio.trades, engine.regimes, "all")["mdd_pct"]
            summary = {"metrics": metrics, "buys": len(buys), "zero_volume_buys": len(impossible),
                       "buys_over_prior_day_1pct": len(over_cap), "buys_missing_prior_day": absent_previous,
                       "stale_holding_days": stale_days, "peak_stale_exposure_pct": round(stale_peak_pct, 4),
                       "stale_missing_position_days": stale_missing, "stale_zero_volume_position_days": stale_zero_volume,
                       "most_stale_tickers": stale_tickers.most_common(5),
                       "terminal_stale_values": stale_last, "terminal_stale_value": round(sum(stale_last.values()), 4),
                       "stale_mark_mdd_50pct_recovery": stressed_mdd["0.5"],
                       "stale_mark_mdd_zero_recovery": stressed_mdd["0"],
                       "terminal_return_50pct_recovery": round((final_equity - .5 * sum(stale_last.values())) / sim.initial_cash * 100 - 100, 4),
                       "terminal_return_zero_recovery": round((final_equity - sum(stale_last.values())) / sim.initial_cash * 100 - 100, 4)}
            results[label][policy] = summary
            dump(out / f"{label}_{policy}_equity.json", portfolio.daily_equity)
            dump(out / f"{label}_{policy}_buys.json", buys)
            print(dataset, label, policy, summary, flush=True)
    dump(out / "results.json", results)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("kr_all", "kr_watch"):
        raise SystemExit("usage: revalidate_kr_liquidity.py kr_all|kr_watch")
    audit(sys.argv[1])
