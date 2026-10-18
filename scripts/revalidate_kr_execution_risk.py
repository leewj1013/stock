"""Causal Korean stale-price reruns and paired extra open-fill slippage scenarios."""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
import socket
import sys

from revalidate_execution_timing import ROOT, BacktestEngine, dump, fixed as bc
from revalidate_sell_timing import SOURCE, OUTPUT as SELL_OUTPUT, next_open_run

OUTPUT = ROOT / "reports/revalidation/2026-10-18-kr-execution-risk"


class ResearchSimulator(bc.PortfolioSimulator):
    stale_factor = 1.0
    extra_slippage_bps = 0

    def _stale(self, ticker, day):
        item = self.engine.by_date.get(ticker, {}).get(day)
        return not item or float(item[1][5]) <= 0

    def _close(self, ticker, day, fallback):
        price = super()._close(ticker, day, fallback)
        return price * self.stale_factor if self._stale(ticker, day) else price

    def _open_mark(self, ticker, day, fallback):
        price = super()._open_mark(ticker, day, fallback)
        return price * self.stale_factor if self._stale(ticker, day) else price


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
    reference = json.loads((SELL_OUTPUT / dataset / "results.json").read_text("utf-8"))
    regimes = ROOT / ("reports/research/scripts/regime_2018" if dataset == "kr_all" else "reports/backtest")
    engine = BacktestEngine(data_dir=data, report_dir=regimes, names=inputs["universe"], score_weights=inputs["weights"], pit_store_path=Path(inputs["pit"]))
    engine._market_up_ratio = lru_cache(maxsize=None)(engine._market_up_ratio)
    days = sorted(candidates)
    out = OUTPUT / dataset
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "inputs.json", {"source": str(SOURCE / dataset), "candidate_sha256": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                               "simulator_sha256": manifest["fixed_sha256"], "last_day": days[-1],
                               "base_cost_pct": engine.cost_pct, "extra_slippage_bps": [0, 25, 50, 100],
                               "stale_valuation_factors": [1, .5, 0]})
    results = {}
    for gate in ([False, True] if dataset == "kr_all" else [True, False]):
        label = "gate_on" if gate else "gate_off"
        results[label] = {}
        for name, factor, slip in (("baseline", 1, 0), ("stale_half", .5, 0), ("stale_zero", 0, 0),
                                   ("slip_25", 1, 25), ("slip_50", 1, 50), ("slip_100", 1, 100)):
            sim = ResearchSimulator(engine, 100_000_000, 10, apply_live_constraints=gate)
            sim.days = days
            sim.stale_factor = factor
            sim.extra_slippage_bps = slip
            sim.run = next_open_run("positive_volume").__get__(sim, type(sim))
            portfolio = sim.run("stock_alarm", candidates, 20260828, liquidate_at_end=False)
            metrics = bc.equity_metrics(portfolio.daily_equity, portfolio.trades, engine.regimes, "all")
            if name == "baseline":
                assert metrics == reference[label]["runs"]["next_open"]["metrics"], "sell-timing baseline changed"
            buys = [e for e in portfolio.events if e["event"] == "buy"]
            sells = [e for e in portfolio.events if e["event"].startswith("sell_")]
            results[label][name] = {"metrics": metrics, "buys": len(buys), "sales": len(sells),
                                    "halt_days": sum(state["defensive_mode"] for state in portfolio.daily_state),
                                    "terminal_positions": portfolio.daily_state[-1]["positions_json"],
                                    "terminal_pending_sells": sim.pending_sell_orders}
            dump(out / f"{label}_{name}_equity.json", portfolio.daily_equity)
            print(dataset, label, name, results[label][name], flush=True)
        dump(out / "results.json", results)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("kr_all", "kr_watch"):
        raise SystemExit("usage: revalidate_kr_execution_risk.py kr_all|kr_watch")
    audit(sys.argv[1])
