from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from statistics import mean, pstdev
from types import SimpleNamespace

from .app import correlation_limited_allocations, load_env, market_exposure_limit_pct, sector_limited_allocations
from .backtest_data import BENCHMARK, DATA_DIR, REPORT_DIR
from .portfolio_risk import evaluate_risk_state
from .risk_release_policy import ExperimentalRiskController
from .sell_check import check_position
from .statistical_validation import benjamini_hochberg, newey_west_mean_test
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine, Trade


OUTPUT_DIR = REPORT_DIR / "benchmark_comparison"
REPORT_PATH = REPORT_DIR / "BENCHMARK_COMPARISON_REPORT.md"


@dataclass
class PortfolioResult:
    strategy: str
    daily_equity: list[dict]
    trades: list[Trade]
    daily_state: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def select_candidate_rows(rows: list[dict], mode: str, top_n: int, seed: int | None = None,
                          minimum_score: float = 0.0) -> list[dict]:
    """Select signals after the common mandatory entry filters have passed."""
    if mode == "stock_alarm":
        eligible = [row for row in rows if float(row["score"]) >= minimum_score]
        return sorted(eligible, key=lambda row: (-float(row["score"]), row["ticker"]))[:top_n]
    if mode == "momentum":
        # Mandatory filters already require close > MA20. Rank the survivors by
        # the current/20-day-average volume ratio without using composite score.
        return sorted(rows, key=lambda row: (-float(row["volume_ratio"]), row["ticker"]))[:top_n]
    if mode == "random":
        ordered = sorted(rows, key=lambda row: row["ticker"])
        return random.Random(seed).sample(ordered, min(top_n, len(ordered)))
    raise ValueError(f"Unknown selection mode: {mode}")


def compound_return(returns: list[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1 + value
    return equity - 1


def simple_momentum_volume_ratio(history: list[list]) -> float | None:
    """Return current/20-day-average volume only when close is above MA20."""
    if len(history) < 21:
        return None
    closes = [float(row[4]) for row in history[-20:]]
    previous_volumes = [float(row[5]) for row in history[-21:-1]]
    average_volume = mean(previous_volumes) if previous_volumes else 0.0
    if average_volume <= 0 or closes[-1] <= mean(closes):
        return None
    return float(history[-1][5]) / average_volume


def equity_metrics(daily_equity: list[dict], trades: list[Trade], regimes: dict[str, str], scope: str) -> dict:
    selected = []
    previous = float(daily_equity[0].get("base_equity", daily_equity[0]["equity"])) if daily_equity else None
    for row in daily_equity:
        equity = float(row["equity"])
        daily_return = 0.0 if previous in (None, 0) else equity / previous - 1
        previous = equity
        if scope == "all" or regimes.get(row["date"]) == scope:
            selected.append((row["date"], daily_return))
    returns = [value for _day, value in selected]
    total = compound_return(returns) if returns else 0.0
    years = max(1 / 252, len(returns) / 252)
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    curve = peak = 1.0
    mdd = 0.0
    for value in returns:
        curve *= 1 + value
        peak = max(peak, curve)
        mdd = min(mdd, curve / peak - 1)
    deviation = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean(returns) / deviation * math.sqrt(252) if deviation else None
    scoped_trades = trades if scope == "all" else [trade for trade in trades if trade.regime == scope]
    wins = [trade.return_pct for trade in scoped_trades if trade.return_pct > 0]
    losses = [trade.return_pct for trade in scoped_trades if trade.return_pct < 0]
    return {
        "regime": scope, "trading_days": len(returns), "trades": len(scoped_trades),
        "total_return_pct": round(total * 100, 4), "cagr_pct": round(cagr * 100, 4),
        "win_rate_pct": round(len(wins) / len(scoped_trades) * 100, 4) if scoped_trades else None,
        "payoff_ratio": round(mean(wins) / abs(mean(losses)), 4) if wins and losses else None,
        "mdd_pct": round(mdd * 100, 4), "sharpe": round(sharpe, 4) if sharpe is not None else None,
    }


def daily_return_map(result: PortfolioResult) -> dict[str, float]:
    output = {}
    previous = float(result.daily_equity[0].get("base_equity", result.daily_equity[0]["equity"])) if result.daily_equity else None
    for row in result.daily_equity:
        equity = float(row["equity"])
        output[row["date"]] = 0.0 if previous in (None, 0) else equity / previous - 1
        previous = equity
    return output


class PortfolioSimulator:
    """Read-only portfolio replay using the live evaluator and sell checker."""

    def __init__(self, engine: BacktestEngine, initial_cash: float, allocation_pct: float,
                 apply_live_constraints: bool = True, risk_release_policy: dict | None = None,
                 apply_market_exposure_limit: bool = True, sell_policy: dict | None = None,
                 target_allocation_pct: float | None = None, max_positions_override: int | None = None,
                 apply_correlation_limit: bool = True, correlation_group_cap_pct: float = 40.0,
                 apply_sector_limit: bool = False, sector_group_cap_pct: float = 100.0,
                 sector_by_ticker: dict[str, str] | None = None):
        self.engine = engine
        self.initial_cash = float(initial_cash)
        self.allocation = float(allocation_pct) / 100
        self.max_positions = max(1, int(1 / self.allocation))
        self.cost_rate = engine.cost_pct / 100
        self.days = sorted(day for day in engine.regimes if day in engine.by_date.get(BENCHMARK, {}))
        self.apply_live_constraints = apply_live_constraints
        self.risk_release_policy = dict(risk_release_policy) if risk_release_policy else None
        self.apply_market_exposure_limit = apply_market_exposure_limit
        self.sell_policy = dict(sell_policy) if sell_policy else None
        self.target_allocation_pct = float(target_allocation_pct) if target_allocation_pct is not None else self.allocation * 100
        self.max_positions_override = max_positions_override
        self.apply_correlation_limit = apply_correlation_limit
        self.correlation_group_cap_pct = float(correlation_group_cap_pct)
        self.apply_sector_limit = apply_sector_limit
        self.sector_group_cap_pct = float(sector_group_cap_pct)
        self.sector_by_ticker = dict(sector_by_ticker or {})

    def build_candidate_cache(self) -> dict[str, list[dict]]:
        cache = {}
        for day in self.days:
            rows = []
            for evaluation in self.engine.passed_evaluations(day):
                if not evaluation.pick:
                    continue
                values = evaluation.values
                rows.append({
                    "ticker": evaluation.pick.ticker, "name": evaluation.pick.name,
                    "score": float(evaluation.pick.score),
                    "volume_ratio": float(values.get("volume_ratio") or 0),
                    "signal_date": day,
                })
            cache[day] = rows
        return cache

    def build_momentum_cache(self) -> dict[str, list[dict]]:
        """Build the deliberately simple MA20-plus-volume benchmark universe."""
        cache = {}
        for day in self.days:
            rows = []
            for ticker, name in self.engine.names.items():
                ratio = simple_momentum_volume_ratio(self.engine._history(ticker, day, 21))
                if ratio is not None:
                    rows.append({"ticker": ticker, "name": name, "score": 0.0, "volume_ratio": ratio, "signal_date": day})
            cache[day] = rows
        return cache

    def run(self, strategy: str, candidates: dict[str, list[dict]], seed: int = 0,
            unconstrained_sizing: bool = False) -> PortfolioResult:
        cash = self.initial_cash
        positions: dict[str, dict] = {}
        pending: dict[str, dict] = {}
        cooldown: dict[str, str] = {}
        trades: list[Trade] = []
        equity_rows = []
        daily_state, events = [], []
        cooldown_days = int(os.environ.get("SELL_RECOMMEND_COOLDOWN_DAYS", "3"))
        position_limit = self.engine.top_n if unconstrained_sizing else (self.max_positions_override or self.max_positions)
        previous_equity = weekly_start_equity = high_water = self.initial_cash
        current_week = None
        previous_risk_status = ""
        risk_controller = ExperimentalRiskController(self.risk_release_policy) if self.risk_release_policy else None
        for day_index, day in enumerate(self.days):
            execution_position_reduced = execution_market_reduced = 0.0
            execution_position_limited = execution_market_limited = 0
            execution_breadth = self.engine._market_up_ratio(day)
            execution_market_limit = market_exposure_limit_pct(execution_breadth) if self.apply_market_exposure_limit else 100.0
            remaining_pending = len(pending)
            for ticker, signal in list(pending.items()):
                item = self.engine.by_date.get(ticker, {}).get(day)
                if not item or float(item[1][1]) <= 0 or len(positions) >= position_limit:
                    pending.pop(ticker)
                    remaining_pending -= 1
                    continue
                open_price = float(item[1][1])
                marked_equity = cash + sum(position["shares"] * self._close(position["ticker"], day, position["entry_price"]) for position in positions.values())
                requested_allocation = max(0.0, float(signal.get("requested_allocation_pct", signal.get("allocation_pct", self.target_allocation_pct)))) / 100
                target_allocation = max(0.0, float(signal.get("allocation_pct", self.target_allocation_pct))) / 100
                invested_before = max(0.0, marked_equity - cash)
                market_budget = max(0.0, marked_equity * execution_market_limit / 100 - invested_before)
                requested_budget = min(cash, marked_equity * requested_allocation)
                position_budget = min(cash, marked_equity * target_allocation)
                budget = cash / max(1, remaining_pending) if unconstrained_sizing else min(position_budget, market_budget)
                if position_budget + 1e-9 < requested_budget:
                    execution_position_limited += 1
                    execution_position_reduced += requested_budget - position_budget
                if budget + 1e-9 < position_budget:
                    execution_market_limited += 1
                    execution_market_reduced += position_budget - budget
                shares = int(budget // open_price)
                pending.pop(ticker)
                remaining_pending -= 1
                if shares < 1:
                    continue
                basis = shares * open_price
                cash -= basis
                positions[ticker] = {
                    **signal, "entry_date": day, "entry_price": open_price, "initial_shares": shares,
                    "shares": shares, "basis": basis, "net_sale_proceeds": 0.0,
                    "entry_equity": marked_equity,
                    "partial_taken": False, "previous_return": None, "max_return": None,
                    "first_take_profit_date": "", "first_take_profit_price": 0,
                }
                events.append({
                    "date": day, "event": "buy", "ticker": ticker, "name": signal["name"],
                    "signal_date": signal["signal_date"], "entry_date": day, "shares": shares,
                    "price": open_price, "gross_notional": basis, "transaction_cost": 0.0,
                    "reason": "entry", "stage": "entry", "portfolio_equity_before": marked_equity,
                    "requested_allocation_pct": requested_allocation * 100,
                    "target_allocation_pct": target_allocation * 100,
                    "actual_allocation_pct": basis / marked_equity * 100 if marked_equity else 0.0,
                    "market_up_ratio": execution_breadth,
                    "market_exposure_limit_pct": execution_market_limit,
                })
            for ticker, position in list(positions.items()):
                history = self.engine._history(ticker, day)
                if len(history) < 20:
                    continue
                close = float(history[-1][4])
                current_return = (close / position["entry_price"] - 1) * 100
                alert = check_position(
                    {"ticker": ticker, "name": position["name"], "entry_price": str(position["entry_price"]), "entry_date": position["entry_date"]},
                    date.fromisoformat(day), position["previous_return"], position["max_return"], history,
                    position["partial_taken"], position["shares"], sell_policy=self.sell_policy,
                )
                position["previous_return"] = current_return
                position["max_return"] = max(position["max_return"] if position["max_return"] is not None else current_return, current_return)
                if not alert:
                    continue
                if alert.sale_type == "partial" and position["shares"] > 1:
                    sold = min(max(1, int(position["shares"] * alert.quantity_fraction)), position["shares"] - 1)
                    equity_before = cash + sum(item["shares"] * self._close(item["ticker"], day, item["entry_price"]) for item in positions.values())
                    cost = position["basis"] * (sold / position["initial_shares"]) * self.cost_rate
                    cash += self._sale_proceeds(position, sold, close)
                    events.append({
                        "date": day, "event": "sell_partial", "ticker": ticker, "name": position["name"],
                        "signal_date": position["signal_date"], "entry_date": position["entry_date"],
                        "entry_price": position["entry_price"], "initial_shares": position["initial_shares"],
                        "sale_fraction_initial": sold / position["initial_shares"], "entry_equity": position["entry_equity"],
                        "shares": sold, "price": close, "gross_notional": sold * close,
                        "transaction_cost": cost, "reason": alert.reason, "stage": alert.stage,
                        "portfolio_equity_before": equity_before,
                    })
                    position["shares"] -= sold
                    position["partial_taken"] = True
                    position["first_take_profit_date"], position["first_take_profit_price"] = day, int(close)
                    continue
                equity_before = cash + sum(item["shares"] * self._close(item["ticker"], day, item["entry_price"]) for item in positions.values())
                cost = position["basis"] * (position["shares"] / position["initial_shares"]) * self.cost_rate
                cash += self._sale_proceeds(position, position["shares"], close)
                events.append({
                    "date": day, "event": "sell_full", "ticker": ticker, "name": position["name"],
                    "signal_date": position["signal_date"], "entry_date": position["entry_date"],
                    "entry_price": position["entry_price"], "initial_shares": position["initial_shares"],
                    "sale_fraction_initial": position["shares"] / position["initial_shares"], "entry_equity": position["entry_equity"],
                    "shares": position["shares"], "price": close,
                    "gross_notional": position["shares"] * close, "transaction_cost": cost,
                    "reason": alert.reason, "stage": alert.stage, "portfolio_equity_before": equity_before,
                })
                total_return = (position["net_sale_proceeds"] / position["basis"] - 1) * 100
                benchmark_return = self.engine._benchmark_trade_return(position["entry_date"], day)
                trades.append(Trade(
                    ticker, position["name"], position["signal_date"], position["entry_date"], day,
                    int(position["entry_price"]), int(close), round(total_return, 4), round(benchmark_return, 4),
                    round(total_return - benchmark_return, 4), self.engine.regimes.get(position["signal_date"], "unclassified"),
                    alert.reason, True, position["first_take_profit_date"], position["first_take_profit_price"],
                ))
                positions.pop(ticker)
                cooldown[ticker] = day
            blocked = set(positions) | set(pending)
            for ticker, sold_day in cooldown.items():
                if (date.fromisoformat(day) - date.fromisoformat(sold_day)).days <= cooldown_days:
                    blocked.add(ticker)
            pre_signal_equity = cash + sum(position["shares"] * self._close(position["ticker"], day, position["entry_price"]) for position in positions.values())
            pre_signal_invested = max(0.0, pre_signal_equity - cash)
            week_key = date.fromisoformat(day).isocalendar()[:2]
            if week_key != current_week:
                weekly_start_equity = previous_equity
                current_week = week_key
            if self.apply_live_constraints:
                risk_args = (
                    {"total_equity": pre_signal_equity, "holdings_value": pre_signal_invested},
                    previous_equity, weekly_start_equity, high_water,
                )
                risk_time = datetime.combine(date.fromisoformat(day), time(15, 40))
                if risk_controller:
                    risk = risk_controller.evaluate(*risk_args, risk_time)
                else:
                    risk = evaluate_risk_state(*risk_args, previous_risk_status, risk_time)
            else:
                risk = {
                    "status": "active", "reason": "", "transition": "", "high_water": max(high_water, pre_signal_equity),
                    "daily_return_pct": (pre_signal_equity / previous_equity - 1) * 100 if previous_equity else 0.0,
                    "weekly_return_pct": (pre_signal_equity / weekly_start_equity - 1) * 100 if weekly_start_equity else 0.0,
                    "drawdown_pct": (pre_signal_equity / max(high_water, pre_signal_equity) - 1) * 100 if pre_signal_equity else 0.0,
                    "exposure_pct": pre_signal_invested / pre_signal_equity * 100 if pre_signal_equity else 0.0,
                }
            high_water = float(risk["high_water"])
            previous_risk_status = str(risk["status"])
            available_slots = position_limit - len(positions) - len(pending)
            eligible = [row for row in candidates.get(day, []) if row["ticker"] not in blocked]
            correlation_rejected = 0
            correlation_limited_count = 0
            correlation_reduced_pct = 0.0
            sector_rejected = 0
            sector_limited_count = 0
            sector_reduced_pct = 0.0
            if available_slots > 0 and risk["status"] in {"active", "reduced"}:
                selected = select_candidate_rows(
                    eligible, strategy, min(self.engine.top_n, available_slots),
                    seed + day_index, self.engine.minimum_score,
                )
                allocation_scale = float(risk.get("allocation_scale", 1.0))
                requested_allocations = [self.target_allocation_pct * allocation_scale] * len(selected)
                allocations = list(requested_allocations)
                if self.apply_live_constraints and self.apply_correlation_limit and selected:
                    existing = []
                    existing_allocations = []
                    for ticker, position in positions.items():
                        value = position["shares"] * self._close(ticker, day, position["entry_price"])
                        existing.append(SimpleNamespace(ticker=ticker))
                        existing_allocations.append(value / pre_signal_equity * 100 if pre_signal_equity else 0.0)
                    combined = existing + [SimpleNamespace(ticker=row["ticker"]) for row in selected]
                    price_rows = {item.ticker: self.engine._history(item.ticker, day, 121) for item in combined}
                    limited = correlation_limited_allocations(
                        combined, existing_allocations + allocations, date.fromisoformat(day),
                        price_rows_by_ticker=price_rows, locked_tickers=set(positions),
                        group_cap_override=self.correlation_group_cap_pct,
                    )
                    allocations = limited[len(existing):]
                    correlation_limited_count = sum(after + 1e-9 < before for before, after in zip(requested_allocations, allocations))
                    correlation_reduced_pct = sum(max(0.0, before - after) for before, after in zip(requested_allocations, allocations))
                    correlation_rejected = sum(before > 0 and after <= 0 for before, after in zip(requested_allocations, allocations))
                if self.apply_live_constraints and self.apply_sector_limit and selected:
                    existing = []
                    existing_allocations = []
                    for ticker, position in positions.items():
                        value = position["shares"] * self._close(ticker, day, position["entry_price"])
                        existing.append(SimpleNamespace(ticker=ticker))
                        existing_allocations.append(value / pre_signal_equity * 100 if pre_signal_equity else 0.0)
                    combined = existing + [SimpleNamespace(ticker=row["ticker"]) for row in selected]
                    before_sector = list(allocations)
                    limited = sector_limited_allocations(
                        combined, existing_allocations + allocations, date.fromisoformat(day),
                        sector_by_ticker=self.sector_by_ticker, locked_tickers=set(positions),
                        group_cap_override=self.sector_group_cap_pct,
                    )
                    allocations = limited[len(existing):]
                    sector_limited_count = sum(after + 1e-9 < before for before, after in zip(before_sector, allocations))
                    sector_reduced_pct = sum(max(0.0, before - after) for before, after in zip(before_sector, allocations))
                    sector_rejected = sum(before > 0 and after <= 0 for before, after in zip(before_sector, allocations))
                for signal, allocation, requested in zip(selected, allocations, requested_allocations):
                    if allocation <= 0:
                        continue
                    signal = {**signal, "allocation_pct": allocation, "requested_allocation_pct": requested}
                    pending[signal["ticker"]] = dict(signal)
            equity = cash + sum(position["shares"] * self._close(position["ticker"], day, position["entry_price"]) for position in positions.values())
            equity_rows.append({"date": day, "equity": round(equity, 6), "base_equity": self.initial_cash})
            invested = max(0.0, equity - cash)
            position_values = {
                ticker: position["shares"] * self._close(ticker, day, position["entry_price"])
                for ticker, position in positions.items()
            }
            if pending:
                idle_reason = "pending_next_open"
            elif len(positions) >= position_limit:
                idle_reason = "position_limit_or_rounding"
            elif not candidates.get(day):
                idle_reason = "no_eligible_signal"
            else:
                idle_reason = "allocation_or_rounding"
            daily_state.append({
                "date": day, "equity": round(equity, 6), "cash": round(cash, 6),
                "cash_weight": cash / equity if equity else 0.0,
                "invested_weight": invested / equity if equity else 0.0,
                "position_count": len(positions), "pending_count": len(pending),
                "defensive_mode": risk["status"] == "halted", "idle_reason": "risk_halt" if risk["status"] == "halted" else idle_reason,
                "risk_status": risk["status"], "risk_reason": risk["reason"], "risk_transition": risk["transition"],
                "risk_raw_reason": risk.get("raw_reason", risk["reason"]),
                "risk_policy_name": risk.get("policy_name", "live_baseline"),
                "risk_policy_mode": risk.get("policy_mode", "baseline"),
                "risk_allocation_scale": risk.get("allocation_scale", 0.0 if risk["status"] == "halted" else 1.0),
                "risk_episode_transition": risk.get("episode_transition", ""),
                "risk_episode_start_day": risk.get("episode_start_day", ""),
                "risk_episode_start_equity": risk.get("episode_start_equity", 0.0),
                "risk_episode_low_equity": risk.get("episode_low_equity", 0.0),
                "risk_episode_trading_days": risk.get("episode_trading_days", 0),
                "risk_episode_released": risk.get("episode_released", False),
                "risk_effective_high_water": risk.get("effective_high_water", risk["high_water"]),
                "risk_peak_decay_amount": risk.get("peak_decay_amount", 0.0),
                "risk_peak_decay_steps_today": risk.get("peak_decay_steps_today", 0),
                "risk_peak_decay_steps_total": risk.get("peak_decay_steps_total", 0),
                "daily_return_pct": risk["daily_return_pct"], "weekly_return_pct": risk["weekly_return_pct"],
                "drawdown_pct": risk["drawdown_pct"], "exposure_pct": risk["exposure_pct"],
                "correlation_rejected_count": correlation_rejected,
                "correlation_limited_count": correlation_limited_count,
                "correlation_reduced_pct": round(correlation_reduced_pct, 4),
                "sector_rejected_count": sector_rejected,
                "sector_limited_count": sector_limited_count,
                "sector_reduced_pct": round(sector_reduced_pct, 4),
                "execution_position_limited_count": execution_position_limited,
                "execution_position_reduced_notional": round(execution_position_reduced, 4),
                "execution_market_limited_count": execution_market_limited,
                "execution_market_reduced_notional": round(execution_market_reduced, 4),
                "market_up_ratio": execution_breadth,
                "market_exposure_limit_pct": execution_market_limit,
                "positions_json": json.dumps({ticker: position["shares"] for ticker, position in positions.items()}, sort_keys=True),
                "position_values_json": json.dumps({ticker: round(value, 6) for ticker, value in position_values.items()}, sort_keys=True),
                "position_weights_json": json.dumps({ticker: value / equity if equity else 0.0 for ticker, value in position_values.items()}, sort_keys=True),
            })
            previous_equity = equity
        if self.days:
            last_day = self.days[-1]
            for ticker, position in list(positions.items()):
                close = self._close(ticker, last_day, position["entry_price"])
                equity_before = cash + sum(item["shares"] * self._close(item["ticker"], last_day, item["entry_price"]) for item in positions.values())
                cost = position["basis"] * (position["shares"] / position["initial_shares"]) * self.cost_rate
                cash += self._sale_proceeds(position, position["shares"], close)
                events.append({
                    "date": last_day, "event": "sell_full", "ticker": ticker, "name": position["name"],
                    "signal_date": position["signal_date"], "entry_date": position["entry_date"],
                    "entry_price": position["entry_price"], "initial_shares": position["initial_shares"],
                    "sale_fraction_initial": position["shares"] / position["initial_shares"], "entry_equity": position["entry_equity"],
                    "shares": position["shares"], "price": close,
                    "gross_notional": position["shares"] * close, "transaction_cost": cost,
                    "reason": "end_of_test", "stage": "end_of_test", "portfolio_equity_before": equity_before,
                })
                total_return = (position["net_sale_proceeds"] / position["basis"] - 1) * 100
                benchmark_return = self.engine._benchmark_trade_return(position["entry_date"], last_day)
                trades.append(Trade(
                    ticker, position["name"], position["signal_date"], position["entry_date"], last_day,
                    int(position["entry_price"]), int(close), round(total_return, 4), round(benchmark_return, 4),
                    round(total_return - benchmark_return, 4), self.engine.regimes.get(position["signal_date"], "unclassified"),
                    "end_of_test", True, position["first_take_profit_date"], position["first_take_profit_price"],
                ))
                positions.pop(ticker)
            equity_rows[-1]["equity"] = round(cash, 6)
            if daily_state:
                daily_state[-1].update({"equity": round(cash, 6), "cash": round(cash, 6), "cash_weight": 1.0,
                                        "invested_weight": 0.0, "position_count": 0, "positions_json": "{}",
                                        "position_values_json": "{}", "position_weights_json": "{}"})
        return PortfolioResult(strategy, equity_rows, trades, daily_state, events)

    def _sale_proceeds(self, position: dict, shares: int, price: float) -> float:
        cost = position["basis"] * (shares / position["initial_shares"]) * self.cost_rate
        proceeds = shares * price - cost
        position["net_sale_proceeds"] += proceeds
        return proceeds

    def _close(self, ticker: str, day: str, fallback: float) -> float:
        item = self.engine.by_date.get(ticker, {}).get(day)
        return float(item[1][4]) if item else float(fallback)

    def buy_and_hold(self, strategy: str, tickers: list[str]) -> PortfolioResult:
        if not self.days:
            return PortfolioResult(strategy, [], [])
        first_day, last_day = self.days[0], self.days[-1]
        cash = self.initial_cash
        positions = {}
        per_ticker = cash / max(1, len(tickers))
        for ticker in tickers:
            item = self.engine.by_date.get(ticker, {}).get(first_day)
            if not item or float(item[1][1]) <= 0:
                continue
            price = float(item[1][1])
            shares = int(per_ticker // price)
            if shares:
                basis = shares * price
                cash -= basis
                positions[ticker] = (shares, basis, price)
        rows = []
        for day in self.days:
            equity = cash + sum(shares * self._close(ticker, day, fallback) for ticker, (shares, _basis, fallback) in positions.items())
            if day == last_day:
                equity -= sum(basis * self.cost_rate for _ticker, (_shares, basis, _fallback) in positions.items())
            rows.append({"date": day, "equity": round(equity, 6), "base_equity": self.initial_cash})
        return PortfolioResult(strategy, rows, [])


def percentile_rank(value: float, distribution: list[float]) -> float:
    if not distribution:
        return 0.0
    return sum(item <= value for item in distribution) / len(distribution) * 100


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    def fmt(value):
        if value is None or value == "":
            return "N/A"
        return f"{value:.4f}" if isinstance(value, float) else str(value)
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def build_report(metrics: list[dict], random_summary: list[dict], comparisons: list[dict], parameters: dict) -> str:
    all_metrics = [row for row in metrics if row["regime"] == "all"]
    stock = next(row for row in all_metrics if row["strategy"] == "stock_alarm")
    random_all = next(row for row in random_summary if row["regime"] == "all")
    random_test = next(row for row in comparisons if row["benchmark"] == "random_mean" and row["regime"] == "all")
    meaningful = stock["total_return_pct"] > random_all["median_total_return_pct"] and random_test["fdr_q_one_sided"] < parameters["alpha"]
    conclusion = (
        "stockAlarm은 랜덤 선택 대비 수익률 우위가 Newey-West 기준으로 유의해 복잡성의 일부 근거가 확인됐다."
        if meaningful else
        "stockAlarm은 랜덤 선택 대비 통계적으로 유의한 우위를 확인하지 못했다. 현재 결과만으로는 복잡성을 정당화하기 어렵고 알고리즘 방향 재검토가 필요하다."
    )
    lines = [
        "# stockAlarm 단순 기준전략 비교", "", "## 기술 요약", "", f"**{conclusion}**", "",
        f"공통 평가기간은 `{parameters['actual_start_date']}`~`{parameters['actual_end_date']}`이고, 랜덤 선택은 고정 시드 `{parameters['random_seed']}`로 {parameters['random_iterations']}회 실행했습니다.",
        "stockAlarm과 랜덤 전략은 같은 필수 매수조건 후보군을 사용하고, 단순 모멘텀은 요청대로 20일선 위 종목을 거래량 비율순으로만 고릅니다. 모든 회전매매 전략은 다음 거래일 시가 진입, 10% 자본 슬롯, 손절·20일선 이탈·수익반납·기간청산·분할익절, 포트폴리오 위험중단과 고상관 그룹 40% 제한을 공유합니다.", "",
        "## 라이브 위험 제약 연결 상태", "",
        f"stockAlarm 경로에서 위험중단은 **{parameters['risk_halt_days']}일**, 중단 진입은 **{parameters['risk_halt_transitions']}회**, 상관제한으로 거절된 신규 신호는 **{parameters['correlation_rejected_signals']}건**입니다.", "",
        "## 전체 기간 비교", "",
    ]
    columns = [("strategy", "전략"), ("trading_days", "거래일"), ("trades", "거래 수"),
               ("total_return_pct", "총수익률%"), ("cagr_pct", "CAGR%"), ("win_rate_pct", "승률%"),
               ("payoff_ratio", "손익비"), ("mdd_pct", "MDD%"), ("sharpe", "Sharpe")]
    lines.extend(_table(all_metrics, columns))
    lines.extend(["", "랜덤 전략은 100회 이상 분포이므로 위 표에는 중앙값을 표시합니다.", "",
                  "## 국면별 비교", ""])
    lines.extend(_table([row for row in metrics if row["regime"] != "all"], [("regime", "국면"), *columns]))
    lines.extend(["", "## 랜덤 몬테카를로 분포와 stockAlarm 위치", ""])
    lines.extend(_table(random_summary, [
        ("regime", "국면"), ("iterations", "반복"), ("p05_total_return_pct", "랜덤 5%"),
        ("median_total_return_pct", "랜덤 중앙값"), ("p95_total_return_pct", "랜덤 95%"),
        ("stock_total_return_pct", "stockAlarm%"), ("stock_percentile", "백분위"),
        ("empirical_p_superiority", "경험적 p(우위)"),
    ]))
    lines.extend(["", "백분위가 높을수록 랜덤 선택보다 좋은 결과입니다. 경험적 p는 랜덤 총수익률이 stockAlarm 이상인 반복 비율에 +1 보정을 적용한 단측 값입니다.", "",
                  "## 일별 수익률 차이 Newey-West 검정", "",
                  "단측 HAC p-value 16개(4개 비교전략×4개 국면)를 하나의 가족으로 BH-FDR 보정했습니다.", ""])
    lines.extend(_table(comparisons, [
        ("regime", "국면"), ("benchmark", "비교전략"), ("days", "일수"),
        ("mean_daily_difference_pct", "일평균 차이%p"), ("hac_lag", "HAC lag"),
        ("hac_p_two_sided", "양측 p"), ("hac_p_one_sided", "우위 단측 p"),
        ("fdr_q_one_sided", "FDR q"), ("significant_superiority", "FDR 유의 우위"),
    ]))
    lines.extend(["", "## 해석과 제안", "", f"**{conclusion}**", "",
                  "- 통계적 유의성과 별개로 총수익률·CAGR·MDD·Sharpe가 함께 개선되는지를 실질적 우위로 판단했습니다.",
                  "- 랜덤보다 유의하게 낫지 않다면 스코어링 가중치 미세조정보다 진입조건·신규 독립요인·포트폴리오 구성 방식 자체를 재검토하는 편이 타당합니다.",
                  "- 결과는 제안 근거일 뿐 운영 가중치나 매수·매도 로직을 자동 변경하지 않습니다.", "",
                  "## 한계와 안전장치", "",
                  "- 현재 watchlist를 과거 전체에 적용하므로 생존편향이 있습니다.",
                  "- 데이터가 2022년부터 시작해 120일 국면 워밍업 뒤부터 모든 전략을 공통 평가했습니다. 따라서 2022년 1월부터의 완전한 투자성과는 아닙니다.",
                  "- 뉴스·공시·재무의 과거 point-in-time 스냅샷이 없어 해당 요소는 미래정보 누출 방지를 위해 0점입니다.",
                  "- 국면별 총수익률은 해당 국면의 일별 수익률만 조건부로 복리 결합한 값이며 독립된 연속 투자구간 수익률이 아닙니다.",
                  "- Buy & Hold에는 거래별 승률·손익비가 없어 N/A로 표시합니다.",
                  "- 라이브 DB에서는 활성 가중치만 읽기 전용으로 스냅샷했으며 DB·가상계좌를 수정하거나 실주문 API를 사용하지 않았습니다.", "",
                  "## 재현 파라미터", "", "```json", json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * probability
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] if lower == upper else ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR) -> dict:
    load_env()
    iterations = max(100, int(os.environ.get("BENCHMARK_RANDOM_ITERATIONS", "100")))
    random_seed = int(os.environ.get("BENCHMARK_RANDOM_SEED", "20260828"))
    initial_cash = float(os.environ.get("BACKTEST_INITIAL_CASH", "100000000"))
    allocation_pct = float(os.environ.get("BACKTEST_TRADE_ALLOCATION_PCT", "10"))
    alpha = float(os.environ.get("STAT_SIGNIFICANCE_ALPHA", "0.05"))
    hac_lag = int(os.environ.get("BENCHMARK_HAC_LAG", "5"))
    weight_snapshot = active_weights()
    engine = BacktestEngine(data_dir, report_dir, score_weights=weight_snapshot)
    simulator = PortfolioSimulator(engine, initial_cash, allocation_pct)
    if not simulator.days:
        raise RuntimeError("No common regime-labeled benchmark days; collect backtest data first")
    candidates = simulator.build_candidate_cache()
    momentum_candidates = simulator.build_momentum_cache()
    stock = simulator.run("stock_alarm", candidates, random_seed)
    momentum = simulator.run("momentum", momentum_candidates, random_seed)
    kospi = simulator.buy_and_hold("kospi_buy_hold", [BENCHMARK])
    equal = simulator.buy_and_hold("equal_weight_buy_hold", list(engine.names))
    random_results = [simulator.run("random", candidates, random_seed + iteration * 100_003) for iteration in range(iterations)]
    regimes = engine.regimes
    metrics = []
    deterministic = [stock, kospi, equal, momentum]
    for result in deterministic:
        for scope in ("all", "bull", "bear", "sideways"):
            metrics.append({"strategy": result.strategy, **equity_metrics(result.daily_equity, result.trades, regimes, scope)})
    random_metrics = {scope: [equity_metrics(result.daily_equity, result.trades, regimes, scope) for result in random_results]
                      for scope in ("all", "bull", "bear", "sideways")}
    random_summary = []
    stock_lookup = {row["regime"]: row for row in metrics if row["strategy"] == "stock_alarm"}
    for scope, rows in random_metrics.items():
        totals = [float(row["total_return_pct"]) for row in rows]
        stock_total = float(stock_lookup[scope]["total_return_pct"])
        summary = {
            "strategy": "random_median", "regime": scope, "iterations": iterations,
            "trading_days": int(_quantile([row["trading_days"] for row in rows], .5)),
            "trades": int(_quantile([row["trades"] for row in rows], .5)),
            "total_return_pct": round(_quantile(totals, .5), 4),
            "cagr_pct": round(_quantile([float(row["cagr_pct"]) for row in rows], .5), 4),
            "win_rate_pct": round(_quantile([float(row["win_rate_pct"] or 0) for row in rows], .5), 4),
            "payoff_ratio": round(_quantile([float(row["payoff_ratio"] or 0) for row in rows], .5), 4),
            "mdd_pct": round(_quantile([float(row["mdd_pct"]) for row in rows], .5), 4),
            "sharpe": round(_quantile([float(row["sharpe"] or 0) for row in rows], .5), 4),
            "p05_total_return_pct": round(_quantile(totals, .05), 4),
            "median_total_return_pct": round(_quantile(totals, .5), 4),
            "p95_total_return_pct": round(_quantile(totals, .95), 4),
            "stock_total_return_pct": stock_total,
            "stock_percentile": round(percentile_rank(stock_total, totals), 2),
            "empirical_p_superiority": round((1 + sum(value >= stock_total for value in totals)) / (iterations + 1), 6),
        }
        random_summary.append(summary)
        metrics.append({key: summary.get(key) for key in ("strategy", "regime", "trading_days", "trades", "total_return_pct", "cagr_pct", "win_rate_pct", "payoff_ratio", "mdd_pct", "sharpe")})
    comparisons = []
    stock_returns = daily_return_map(stock)
    comparison_results = {result.strategy: result for result in (kospi, equal, momentum)}
    mean_random_returns = {}
    random_maps = [daily_return_map(result) for result in random_results]
    for day in stock_returns:
        values = [item[day] for item in random_maps if day in item]
        mean_random_returns[day] = mean(values) if values else 0.0
    for benchmark_name, benchmark_returns in [
        *[(name, daily_return_map(result)) for name, result in comparison_results.items()],
        ("random_mean", mean_random_returns),
    ]:
        for scope in ("all", "bull", "bear", "sideways"):
            days = [day for day in stock_returns if day in benchmark_returns and (scope == "all" or regimes.get(day) == scope)]
            differences = [(stock_returns[day] - benchmark_returns[day]) * 100 for day in days]
            two_sided = newey_west_mean_test(differences, hac_lag, "two-sided")
            one_sided = newey_west_mean_test(differences, hac_lag, "greater")
            comparisons.append({
                "regime": scope, "benchmark": benchmark_name, "days": len(days),
                "mean_daily_difference_pct": round(float(two_sided["mean"] or 0), 6), "hac_lag": two_sided["lag"],
                "hac_p_two_sided": round(float(two_sided["p_value"]), 6),
                "hac_p_one_sided": round(float(one_sided["p_value"]), 6),
                "fdr_q_one_sided": None, "significant_superiority": False,
            })
    adjusted = benjamini_hochberg([row["hac_p_one_sided"] for row in comparisons])
    for row, q_value in zip(comparisons, adjusted):
        row["fdr_q_one_sided"] = round(float(q_value), 6) if q_value is not None else None
        row["significant_superiority"] = bool(row["mean_daily_difference_pct"] > 0 and q_value is not None and q_value < alpha)
    parameters = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "requested_start_date": "2022-01-01",
        "actual_start_date": simulator.days[0], "actual_end_date": simulator.days[-1],
        "random_iterations": iterations, "random_seed": random_seed, "initial_cash": initial_cash,
        "trade_allocation_pct": allocation_pct, "max_positions": simulator.max_positions,
        "top_n": engine.top_n, "execution_cost_pct": engine.cost_pct, "hac_lag": hac_lag, "alpha": alpha,
        "score_weights": weight_snapshot, "data_dir": str(data_dir),
        "portfolio_risk_gate_enabled": True, "correlation_group_limit_enabled": True,
        "market_exposure_limit_enabled": True,
        "risk_halt_days": sum(bool(row.get("defensive_mode")) for row in stock.daily_state),
        "risk_halt_transitions": sum(row.get("risk_transition") == "halted" for row in stock.daily_state),
        "correlation_rejected_signals": sum(int(row.get("correlation_rejected_count") or 0) for row in stock.daily_state),
        "live_database_access": "read_only_active_weight_snapshot", "live_configuration_changed": False,
    }
    _write_csv(OUTPUT_DIR / "strategy_metrics.csv", metrics)
    _write_csv(OUTPUT_DIR / "random_distribution.csv", [
        {"iteration": index, **metric}
        for index, result in enumerate(random_results)
        for metric in [equity_metrics(result.daily_equity, result.trades, regimes, "all")]
    ])
    _write_csv(OUTPUT_DIR / "random_summary.csv", random_summary)
    _write_csv(OUTPUT_DIR / "newey_west_comparisons.csv", comparisons)
    _write_csv(OUTPUT_DIR / "stock_alarm_trades.csv", [asdict(trade) for trade in stock.trades])
    _write_csv(OUTPUT_DIR / "stock_alarm_daily_state.csv", stock.daily_state)
    _write_csv(OUTPUT_DIR / "stock_alarm_events.csv", stock.events)
    (OUTPUT_DIR / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(build_report(metrics, random_summary, comparisons, parameters), encoding="utf-8")
    result = {
        "report": str(REPORT_PATH), "random_iterations": iterations,
        "stock_total_return_pct": stock_lookup["all"]["total_return_pct"],
        "stock_random_percentile": next(row["stock_percentile"] for row in random_summary if row["regime"] == "all"),
        "live_database_access": "read_only_active_weight_snapshot",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare stockAlarm with simple benchmark strategies")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
