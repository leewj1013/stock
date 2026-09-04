from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from statistics import mean

from .app import _scale, apply_relative_strength, category_scores, configured_stocks, evaluate_naver_candidate, env_float, load_env, market_up_ratio, profile_total_score
from .backtest_data import BENCHMARK, DATA_DIR, REPORT_DIR, read_rows
from .point_in_time_store import DEFAULT_PATH as PIT_DEFAULT_PATH, PointInTimeStore
from .sell_check import check_position
from .strategy_learning import DEFAULT_WEIGHTS, FACTORS, objective, walk_forward_validate
from .trading_profiles import CATEGORY_VALUE_KEYS


@dataclass
class Trade:
    ticker: str
    name: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: int
    exit_price: int
    return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    regime: str
    exit_reason: str
    partial_profit: bool
    first_take_profit_date: str = ""
    first_take_profit_price: int = 0


def _date_key(row: list) -> str:
    return datetime.strptime(str(row[0]), "%Y%m%d").date().isoformat()


def _metrics(rows: list[Trade]) -> dict[str, float | int | None]:
    values = [row.return_pct for row in sorted(rows, key=lambda item: item.exit_date)]
    wins, losses = [value for value in values if value > 0], [value for value in values if value < 0]
    equity = peak = 100.0
    mdd = 0.0
    allocation = max(0.01, min(1.0, float(os.environ.get("BACKTEST_TRADE_ALLOCATION_PCT", "10")) / 100))
    for value in values:
        # Signal-level trades can overlap. Apply a fixed capital allocation per
        # trade instead of unrealistically compounding the full account on each.
        equity *= 1 + value / 100 * allocation
        peak = max(peak, equity)
        mdd = min(mdd, (equity - peak) / peak * 100)
    return {
        "samples": len(rows),
        "win_rate_pct": round(len(wins) / len(values) * 100, 2) if values else 0.0,
        "payoff_ratio": round(mean(wins) / abs(mean(losses)), 4) if wins and losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else None,
        "avg_return_pct": round(mean(values), 4) if values else 0.0,
        "total_return_pct": round(equity - 100, 4),
        "mdd_pct": round(mdd, 4),
        "avg_excess_return_pct": round(mean(row.excess_return_pct for row in rows), 4) if rows else 0.0,
        "partial_exit_count": sum(bool(row.first_take_profit_date) for row in rows),
    }


def summarize_by_regime(trades: list[Trade], variant: str) -> list[dict]:
    output = []
    for regime in ("all", "bull", "bear", "sideways"):
        selected = trades if regime == "all" else [trade for trade in trades if trade.regime == regime]
        output.append({"variant": variant, "regime": regime, **_metrics(selected)})
    return output


class BacktestEngine:
    def __init__(self, data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR, score_weights: dict[str, float] | None = None,
                 names: dict[str, str] | None = None, pit_store_path: Path | None = None,
                 profile: dict | None = None):
        self.data_dir, self.report_dir = data_dir, report_dir
        self.names = dict(names) if names is not None else configured_stocks()
        self.rows = {ticker: read_rows(ticker, data_dir) for ticker in [BENCHMARK, *self.names]}
        self.by_date = {ticker: {_date_key(row): (index, row) for index, row in enumerate(rows)} for ticker, rows in self.rows.items()}
        self.regimes = self._read_regimes(report_dir / "regime_labels.csv")
        self.weights = {factor: float((score_weights or DEFAULT_WEIGHTS).get(factor, DEFAULT_WEIGHTS[factor])) for factor in FACTORS}
        # Optional profile-scoring re-rank: candidates still have to clear the
        # same quality gate (self.minimum_score on the existing factor score)
        # -- a profile's category weights only change which of those already
        # passed a profile actually picks, matching select_for_profile in
        # app.py's live pipeline.
        self.profile = profile
        self.top_n = int(os.environ.get("TOP_N", "5"))
        self.minimum_score = env_float("MIN_RECOMMEND_SCORE", 50)
        self.min_trading_value = int(os.environ.get("MIN_TRADING_VALUE", "5000000000"))
        self.volume_multiplier = env_float("VOLUME_MULTIPLIER", 1.5)
        self.market_ratio = env_float("MIN_MARKET_UP_RATIO", 0.45)
        self.cost_pct = (int(os.environ.get("EXECUTION_COST_BPS", "30")) + int(os.environ.get("BACKTEST_SLIPPAGE_BPS", "10"))) / 100
        configured_pit = os.environ.get("PIT_STORE_PATH", "").strip()
        resolved_pit = pit_store_path or (Path(configured_pit) if configured_pit else PIT_DEFAULT_PATH)
        self.pit_store = PointInTimeStore(resolved_pit) if resolved_pit.exists() else None

    @staticmethod
    def _read_regimes(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        with path.open(newline="", encoding="utf-8-sig") as file:
            return {row["date"]: row["regime"] for row in csv.DictReader(file)}

    def _history(self, ticker: str, day: str, length: int = 91) -> list[list]:
        item = self.by_date.get(ticker, {}).get(day)
        if not item:
            return []
        index, _row = item
        return self.rows[ticker][max(0, index - length + 1):index + 1]

    def _market_up_ratio(self, day: str) -> float:
        moves = []
        for ticker in self.names:
            item = self.by_date.get(ticker, {}).get(day)
            if item and item[0] > 0:
                index = item[0]
                moves.append(int(self.rows[ticker][index][4]) > int(self.rows[ticker][index - 1][4]))
        return market_up_ratio(moves)

    def _benchmark_return(self, day: str) -> float:
        item = self.by_date.get(BENCHMARK, {}).get(day)
        if not item or item[0] < 1:
            return 0.0
        index = item[0]
        previous, close = float(self.rows[BENCHMARK][index - 1][4]), float(self.rows[BENCHMARK][index][4])
        return (close / previous - 1) * 100 if previous else 0.0

    def passed_evaluations(self, day: str, blocked: set[str] | None = None) -> list:
        """Return every mandatory-filter pass using the live scoring path.

        No score threshold or TOP_N selection is applied here.  This public
        boundary lets validation tools inspect raw factors without duplicating
        the live evaluator or accidentally touching the live database.
        """
        if self._market_up_ratio(day) < self.market_ratio:
            return []
        blocked = blocked or set()
        evaluations = []
        for ticker, name in self.names.items():
            if ticker in blocked:
                continue
            history = self._history(ticker, day)
            if len(history) < 21:
                continue
            evaluation = evaluate_naver_candidate(
                ticker, name, date.fromisoformat(day), self.min_trading_value, self.volume_multiplier,
                price_rows=history, external_lookup=False, score_weights=self.weights,
            )
            if evaluation.pick:
                if self.pit_store:
                    pit = self.pit_store.scores_asof(ticker, date.fromisoformat(day))
                    values = dict(evaluation.values)
                    values.update(pit)
                    values.update({f"pit_{source}_available": bool(pit["pit_sources"][source]) for source in ("news", "disclosure", "financial", "financial_statement")})
                    pick = replace(
                        evaluation.pick,
                        news_score=float(pit["news_score"]),
                        disclosure_score=float(pit["disclosure_score"]),
                        financial_score=float(pit["financial_score"]),
                    )
                    # Category scores were computed against the disabled
                    # (external_lookup=False) placeholders above; redo them
                    # against the real point-in-time news/disclosure/financial
                    # values so profile backtests aren't ranking on frozen
                    # constants. financial_ratios is real DART ROE/growth/
                    # debt-ratio when a filing was on record as of this day;
                    # otherwise category_scores falls back to financial_score/
                    # disclosure sentiment the same way the live pipeline does.
                    values.update(category_scores(
                        pit["financial_ratios"], pit["financial_score"], pit["disclosure_score"], pit["dividend_yield"], values.get("atr20_pct") or 0,
                    ))
                    values["news_category_score"] = round(_scale(pit["news_score"], -3, 3), 2)
                    evaluation = replace(evaluation, values=values, pick=pick)
                evaluations.append(evaluation)
        return apply_relative_strength(evaluations, (BENCHMARK, self._benchmark_return(day)), self.weights)

    def candidates(self, day: str, blocked: set[str], open_count: int = 0) -> list[dict]:
        evaluations = self.passed_evaluations(day, blocked)
        passing = [item for item in evaluations if item.pick and item.pick.score >= self.minimum_score]
        room = self.top_n
        if self.profile:
            max_volatility = self.profile.get("max_volatility_atr_pct")
            if max_volatility is not None:
                passing = [item for item in passing if (item.values.get("atr20_pct") or 0) <= max_volatility]
            weights = self.profile.get("scoring_weights")
            rank_key = (lambda item: profile_total_score(item.values, weights)) if weights else (lambda item: item.pick.score)
            max_holdings = self.profile.get("max_holdings")
            if max_holdings is not None:
                room = max(0, min(self.top_n, max_holdings - open_count))
        else:
            rank_key = lambda item: item.pick.score
        selected = sorted(passing, key=rank_key, reverse=True)[:room]
        output = []
        for item in selected:
            pick = item.pick
            factors = {factor: float(item.values.get(factor) or 0) for factor in FACTORS}
            output.append({"ticker": pick.ticker, "name": pick.name, "signal_date": day, "score": pick.score, "factors": factors})
        return output

    def _benchmark_trade_return(self, entry_day: str, exit_day: str) -> float:
        entry = self.by_date.get(BENCHMARK, {}).get(entry_day)
        exit_item = self.by_date.get(BENCHMARK, {}).get(exit_day)
        if not entry or not exit_item:
            return 0.0
        opening, close = float(entry[1][1]), float(exit_item[1][4])
        return (close / opening - 1) * 100 if opening else 0.0

    def forward_outcomes(self, ticker: str, signal_day: str, horizons: tuple[int, ...] = (1, 3, 5, 10, 20)) -> dict:
        """Calculate next-session-open forward returns without external state.

        The definition matches the isolated validation backtest: buy at the
        next available session open, value at the close ``horizon`` sessions
        later, and subtract configured execution cost/slippage once.
        """
        signal = self.by_date.get(ticker, {}).get(signal_day)
        if not signal or signal[0] + 1 >= len(self.rows.get(ticker, [])):
            return {}
        entry_index = signal[0] + 1
        entry_row = self.rows[ticker][entry_index]
        entry_price = float(entry_row[1])
        entry_day = _date_key(entry_row)
        benchmark_entry = self.by_date.get(BENCHMARK, {}).get(entry_day)
        if entry_price <= 0 or not benchmark_entry or float(benchmark_entry[1][1]) <= 0:
            return {}
        values: dict[str, float | str | None] = {
            "entry_date": entry_day,
            "entry_price": entry_price,
        }
        benchmark_open = float(benchmark_entry[1][1])
        for horizon in horizons:
            stock_return = benchmark_return = None
            if entry_index + horizon < len(self.rows[ticker]):
                close = float(self.rows[ticker][entry_index + horizon][4])
                stock_return = (close / entry_price - 1) * 100 - self.cost_pct
            if benchmark_entry[0] + horizon < len(self.rows[BENCHMARK]):
                benchmark_close = float(self.rows[BENCHMARK][benchmark_entry[0] + horizon][4])
                benchmark_return = (benchmark_close / benchmark_open - 1) * 100
            values[f"return_{horizon}d_pct"] = stock_return
            values[f"benchmark_{horizon}d_pct"] = benchmark_return
            values[f"excess_{horizon}d_pct"] = stock_return - benchmark_return if stock_return is not None and benchmark_return is not None else None
        return values

    def category_training_rows(self, horizon: int = 5) -> list[tuple[str, dict, float]]:
        """(day, category_scores, objective_return) for every candidate that
        passed the shared quality filter on every backtest day -- not just
        the ones a particular profile went on to select. This is the
        population profile_weight_learning.learn_profile_weights() correlates
        category scores against, mirroring how strategy_learning.py learns
        the global factor weights but reusing forward_outcomes() (fixed
        N-day-ahead return) instead of full sell-rule trade simulation, since
        we need every passing candidate's outcome, not just the ones actually
        traded.
        """
        benchmark_days = sorted(day for day in self.regimes if day in self.by_date.get(BENCHMARK, {}))
        rows = []
        for day in benchmark_days:
            for item in self.passed_evaluations(day, blocked=set()):
                if item.pick.score < self.minimum_score:
                    continue
                categories = {category: float(item.values.get(key) or 0) for category, key in CATEGORY_VALUE_KEYS.items()}
                outcome = self.forward_outcomes(item.pick.ticker, day, (horizon,))
                value = outcome.get(f"excess_{horizon}d_pct")
                if value is not None:
                    rows.append((day, categories, value))
        return rows

    def _learning_row(self, position: dict) -> dict:
        ticker, entry_day, entry_price = position["ticker"], position["entry_date"], position["entry_price"]
        item = self.by_date[ticker].get(entry_day)
        benchmark_item = self.by_date[BENCHMARK].get(entry_day)
        values = {}
        for horizon in (1, 3, 5, 10):
            stock_return = benchmark_return = None
            if item and item[0] + horizon < len(self.rows[ticker]):
                close = float(self.rows[ticker][item[0] + horizon][4])
                stock_return = (close / entry_price - 1) * 100 - self.cost_pct
            if benchmark_item and benchmark_item[0] + horizon < len(self.rows[BENCHMARK]):
                benchmark_open = float(benchmark_item[1][1])
                benchmark_close = float(self.rows[BENCHMARK][benchmark_item[0] + horizon][4])
                benchmark_return = (benchmark_close / benchmark_open - 1) * 100 if benchmark_open else None
            values[f"return_{horizon}d_pct"] = stock_return
            values[f"excess_{horizon}d_pct"] = stock_return - benchmark_return if stock_return is not None and benchmark_return is not None else None
        return {
            "pick_date": position["signal_date"], "ticker": ticker,
            "factors_json": json.dumps(position["factors"], sort_keys=True), **values,
        }

    def run(self, partial_profit: bool) -> tuple[list[Trade], list[dict]]:
        benchmark_days = sorted(day for day in self.regimes if day in self.by_date.get(BENCHMARK, {}))
        positions, pending, cooldown, trades, learning_rows = {}, {}, {}, [], []
        for day in benchmark_days:
            for ticker, signal in list(pending.items()):
                item = self.by_date.get(ticker, {}).get(day)
                if not item or int(item[1][1]) <= 0:
                    continue
                positions[ticker] = {
                    **signal, "entry_date": day, "entry_price": int(item[1][1]), "quantity": 100,
                    "partial_taken": False, "realized_return": 0.0, "previous_return": None,
                    "max_return": None, "first_take_profit_date": "", "first_take_profit_price": 0,
                }
                pending.pop(ticker)
            for ticker, position in list(positions.items()):
                history = self._history(ticker, day)
                if len(history) < 20:
                    continue
                close = int(history[-1][4])
                current_return = (close / position["entry_price"] - 1) * 100
                alert = check_position(
                    {"ticker": ticker, "name": position["name"], "entry_price": str(position["entry_price"]), "entry_date": position["entry_date"]},
                    date.fromisoformat(day), position["previous_return"], position["max_return"], history,
                    partial_profit and position["partial_taken"],
                    position["quantity"] if partial_profit else 0,
                )
                position["previous_return"] = current_return
                position["max_return"] = max(position["max_return"] if position["max_return"] is not None else current_return, current_return)
                if not alert:
                    continue
                if alert.sale_type == "partial" and partial_profit:
                    sold = min(max(1, int(position["quantity"] * alert.quantity_fraction)), position["quantity"] - 1)
                    position["realized_return"] += sold / 100 * current_return
                    position["quantity"] -= sold
                    position["partial_taken"] = True
                    position["first_take_profit_date"], position["first_take_profit_price"] = day, close
                    continue
                total_return = position["realized_return"] + position["quantity"] / 100 * current_return - self.cost_pct
                benchmark_return = self._benchmark_trade_return(position["entry_date"], day)
                trades.append(Trade(
                    ticker, position["name"], position["signal_date"], position["entry_date"], day,
                    position["entry_price"], close, round(total_return, 4), round(benchmark_return, 4),
                    round(total_return - benchmark_return, 4), self.regimes.get(position["signal_date"], "unclassified"),
                    alert.reason, partial_profit, position["first_take_profit_date"], position["first_take_profit_price"],
                ))
                learning_rows.append(self._learning_row(position))
                positions.pop(ticker)
                cooldown[ticker] = day
            blocked = set(positions) | set(pending)
            for ticker, sold_day in cooldown.items():
                if (date.fromisoformat(day) - date.fromisoformat(sold_day)).days <= int(env_float("SELL_RECOMMEND_COOLDOWN_DAYS", 3)):
                    blocked.add(ticker)
            for signal in self.candidates(day, blocked, open_count=len(positions) + len(pending)):
                pending[signal["ticker"]] = signal
        if benchmark_days:
            last_day = benchmark_days[-1]
            for ticker, position in list(positions.items()):
                item = self.by_date.get(ticker, {}).get(last_day)
                if not item:
                    continue
                close = int(item[1][4])
                current_return = (close / position["entry_price"] - 1) * 100
                total_return = position["realized_return"] + position["quantity"] / 100 * current_return - self.cost_pct
                benchmark_return = self._benchmark_trade_return(position["entry_date"], last_day)
                trades.append(Trade(
                    ticker, position["name"], position["signal_date"], position["entry_date"], last_day,
                    position["entry_price"], close, round(total_return, 4), round(benchmark_return, 4),
                    round(total_return - benchmark_return, 4), self.regimes.get(position["signal_date"], "unclassified"),
                    "end_of_test", partial_profit, position["first_take_profit_date"], position["first_take_profit_price"],
                ))
                learning_rows.append(self._learning_row(position))
        return trades, learning_rows


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        if not columns:
            return
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _report(summary: list[dict], validation: dict, parameters: dict, quality_rows: list[dict]) -> str:
    def table(rows: list[dict], columns: list[str]) -> list[str]:
        return ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|", *["| " + " | ".join(str(row.get(column, "")) for column in columns) + " |" for row in rows]]
    lines = [
        "# stockAlarm 3년+ 전략 검증 백테스트", "", f"생성 시각: {datetime.now().isoformat(timespec='seconds')}", "",
        "## 결론", "", f"강화된 승격 게이트 판정: **{validation.get('status')}**", f"유효 학습 표본: **{validation.get('sample_count', 0)}건**", f"통합 p-value: **{validation.get('p_value', 'N/A')}**", f"판단 사유: `{validation.get('decision_reason', '')}`", "",
        "## 국면별 및 전체 성과", "",
    ]
    columns = ["variant", "regime", "samples", "win_rate_pct", "payoff_ratio", "avg_return_pct", "mdd_pct", "avg_excess_return_pct", "partial_exit_count"]
    lines.extend(table(summary, columns))
    lines.extend(["", "## 분할익절 적용 효과", ""])
    comparisons = []
    for regime in ("all", "bull", "bear", "sideways"):
        before = next(row for row in summary if row["variant"] == "without_partial_profit" and row["regime"] == regime)
        after = next(row for row in summary if row["variant"] == "with_partial_profit" and row["regime"] == regime)
        comparisons.append({
            "regime": regime, "sample_delta": after["samples"] - before["samples"],
            "win_rate_delta_pp": round(after["win_rate_pct"] - before["win_rate_pct"], 4),
            "avg_return_delta_pp": round(after["avg_return_pct"] - before["avg_return_pct"], 4),
            "mdd_delta_pp": round(after["mdd_pct"] - before["mdd_pct"], 4),
            "excess_return_delta_pp": round(after["avg_excess_return_pct"] - before["avg_excess_return_pct"], 4),
        })
    lines.extend(table(comparisons, ["regime", "sample_delta", "win_rate_delta_pp", "avg_return_delta_pp", "mdd_delta_pp", "excess_return_delta_pp"]))
    lines.extend(["", "## Walk-forward 구간", ""])
    if validation.get("folds") and all(fold.get("baseline_return") == fold.get("proposed_return") for fold in validation["folds"]):
        lines.extend(["세 구간 모두 신규 가중치가 실제 선정 순위를 바꾸지 못해 기존 전략과 동일한 성과를 냈습니다. 따라서 통계적 개선 근거가 없습니다.", ""])
    lines.append("`baseline_return`과 `proposed_return`은 가능한 경우 벤치마크 대비 초과수익률을 우선 사용한 학습 목적값입니다.")
    lines.append("")
    fold_columns = ["fold", "sample_count", "selected_count", "win_rate_pct", "profit_factor", "baseline_return", "proposed_return", "baseline_mdd", "proposed_mdd", "p_value", "passed"]
    lines.extend(table(validation.get("folds") or [], fold_columns))
    reason_counts = {}
    for row in quality_rows:
        reason_counts[row.get("reason", "unknown")] = reason_counts.get(row.get("reason", "unknown"), 0) + 1
    lines.extend(["", "## 데이터 품질과 제외", "", f"품질 문제 또는 누락 세션 기록: **{len(quality_rows)}건**", ""])
    lines.extend(table([{"reason": key, "count": value} for key, value in sorted(reason_counts.items())], ["reason", "count"]))
    quality_periods = []
    for reason in sorted(reason_counts):
        matching = [row for row in quality_rows if row.get("reason") == reason]
        dates = sorted(row.get("date", "") for row in matching if row.get("date"))
        quality_periods.append({
            "reason": reason, "first_date": dates[0] if dates else "", "last_date": dates[-1] if dates else "",
            "affected_tickers": len({row.get("ticker") for row in matching if row.get("ticker")}),
        })
    lines.extend(["", "제외·누락 기간 범위:", ""])
    lines.extend(table(quality_periods, ["reason", "first_date", "last_date", "affected_tickers"]))
    lines.extend([
        "", "## 재현 파라미터", "", "```json", json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True), "```", "",
        "## 해석 시 주의사항", "",
        "- 생존편향: 현재 watchlist를 과거 전 기간에 적용합니다.",
        "- 과거 시점 뉴스·DART 공시·재무 스냅샷이 없어 미래정보 누출을 방지하기 위해 해당 점수는 0으로 두었습니다.",
        "- 종목별 거래 결과를 순차 결합한 신호 수준 백테스트이며 실제 동시 포트폴리오 체결과 차이가 있습니다.",
        "- 성과표 MDD는 신호당 10% 고정 자본배분을 사용하지만, Walk-forward MDD는 라이브 승격 로직과 동일한 선택 신호 수익률 연속값입니다.",
        "- 네이버 일봉은 실시간 호가·체결 데이터를 재현하지 못하며, 거래비용과 슬리피지는 설정값으로 단순화했습니다.",
        "- p-value는 과최적화가 없음을 보장하지 않으며, 실계좌 전환 전 별도의 완전 미사용 기간(out-of-sample) 검증이 필요합니다.",
    ])
    return "\n".join(lines) + "\n"


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR) -> dict:
    load_env()
    engine = BacktestEngine(data_dir, report_dir)
    if not engine.rows.get(BENCHMARK) or not engine.regimes:
        raise RuntimeError("Backtest data is missing. Run: python -m stock_alarm.backtest_data")
    without_partial, learning_rows = engine.run(False)
    with_partial, _ = engine.run(True)
    usable = [(row, objective(row)) for row in learning_rows]
    usable = [(row, float(value)) for row, value in usable if value is not None]
    validation = walk_forward_validate(
        usable, engine.weights,
        int(os.environ.get("LEARNING_MIN_SAMPLES", "300")),
        int(os.environ.get("LEARNING_VALIDATION_MIN_SAMPLES", "60")),
        int(os.environ.get("LEARNING_VALIDATION_FOLDS", "3")),
        float(os.environ.get("LEARNING_SIGNIFICANCE_LEVEL", "0.05")),
        float(os.environ.get("LEARNING_MAX_DAILY_WEIGHT_CHANGE", "0.05")),
    )
    summary = summarize_by_regime(without_partial, "without_partial_profit") + summarize_by_regime(with_partial, "with_partial_profit")
    parameters = {
        key: os.environ.get(key, default) for key, default in {
            "BACKTEST_START_DATE": "2022-01-01", "BACKTEST_END_DATE": date.today().isoformat(),
            "TOP_N": "5", "MIN_TRADING_VALUE": "5000000000", "VOLUME_MULTIPLIER": "1.5",
            "MIN_RECOMMEND_SCORE": "50", "MIN_MARKET_UP_RATIO": "0.45", "EXECUTION_COST_BPS": "30",
            "BACKTEST_SLIPPAGE_BPS": "10", "TAKE_PROFIT_1_PCT": "10", "TAKE_PROFIT_1_SELL_RATIO": "50",
            "BACKTEST_TRADE_ALLOCATION_PCT": "10",
            "TAKE_PROFIT_2_PCT": "20", "SELL_LOSS_PCT": "5", "SELL_ATR_MULTIPLIER": "2",
            "SELL_TIME_STOP_DAYS": "10", "LEARNING_MIN_SAMPLES": "300", "LEARNING_VALIDATION_MIN_SAMPLES": "60",
            "LEARNING_VALIDATION_FOLDS": "3", "LEARNING_SIGNIFICANCE_LEVEL": "0.05",
        }.items()
    }
    parameters["baseline_weights"] = engine.weights
    parameters["proposed_weights"] = validation.get("weights", engine.weights)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(report_dir / "trades_without_partial_profit.csv", [asdict(row) for row in without_partial], list(Trade.__annotations__))
    _write_csv(report_dir / "trades_with_partial_profit.csv", [asdict(row) for row in with_partial], list(Trade.__annotations__))
    _write_csv(report_dir / "performance_summary.csv", summary)
    _write_csv(report_dir / "walk_forward_folds.csv", validation.get("folds") or [], list((validation.get("folds") or [{}])[0]))
    (report_dir / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    quality_path = report_dir / "data_quality.csv"
    if quality_path.exists():
        with quality_path.open(newline="", encoding="utf-8-sig") as file:
            quality_rows = list(csv.DictReader(file))
    else:
        quality_rows = []
    (report_dir / "REPORT.md").write_text(_report(summary, validation, parameters, quality_rows), encoding="utf-8")
    result = {"without_partial": len(without_partial), "with_partial": len(with_partial), "validation": validation["status"], "report": str(report_dir / "REPORT.md")}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
