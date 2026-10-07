"""Forward US strategy account: the backtest engine replayed from a fixed start day on the frozen top-100 US universe.

State is derived, not stored: every run refreshes the daily bars, replays the engine from START_SIGNAL_DAY and rewrites
the ledgers. Two guards keep it a record rather than a moving backtest: the strategy settings are frozen on the first
run, and every order ever logged in us_strategy_decisions.csv must be reproduced by the replay -- otherwise nothing is
overwritten and the status file says why. Reads/writes data/ files only; no live DB is opened for writing.
"""
import csv
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

DATA = Path("data")
SRC = DATA / "backtest_us" / "ohlcv"
WORK = DATA / "backtest_us" / "account_work"  # integer-cent copies the engine reads, plus regime labels
UNIVERSE = DATA / "us_strategy_universe.csv"
CONFIG = DATA / "us_strategy_config.json"
EQUITY, TRADES = DATA / "us_strategy_equity.csv", DATA / "us_strategy_trades.csv"
HOLDINGS, DECISIONS = DATA / "us_strategy_holdings.csv", DATA / "us_strategy_decisions.csv"
STATUS = DATA / "us_strategy_status.json"

START_SIGNAL_DAY = "2026-10-06"  # first signal made at this close, first fill at the next open
CAPITAL_USD = 100_000
EXCLUDE = {"AZN"}  # mixed listings, see data/backtest_us/QUALITY.md
MIN_TRADING_VALUE_CENTS = "5000000000"  # $50M
FROZEN_ENV = ("TOP_N", "MIN_RECOMMEND_SCORE", "VOLUME_MULTIPLIER", "MIN_MARKET_UP_RATIO", "EXECUTION_COST_BPS",
              "BACKTEST_SLIPPAGE_BPS", "MAX_MA20_DISTANCE_ATR", "SELL_RECOMMEND_COOLDOWN_DAYS")
DECISION_FIELDS = ["date", "event", "ticker", "shares", "price_usd", "reason"]


def current_config() -> dict:
    from .strategy_learning import active_weights
    env = {k: os.environ.get(k, "") for k in FROZEN_ENV}
    env.update({k: v for k, v in sorted(os.environ.items()) if k.startswith("SELL_")})
    return {"weights": active_weights(), "env": env, "capital_usd": CAPITAL_USD, "start_signal_day": START_SIGNAL_DAY}


def write_status(**fields) -> None:
    STATUS.write_text(json.dumps({"updated_at": datetime.now().isoformat(timespec="seconds"), **fields}, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _convert(code: str, out: str) -> None:
    """USD bars -> integer cents (the engine is integer based); repair bars whose high/low do not bracket open/close."""
    with open(WORK / "ohlcv" / f"{out}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for r in csv.DictReader(open(SRC / f"{code}.csv", encoding="utf-8")):
            o, h, l, c = (round(float(r[k]) * 100) for k in ("open", "high", "low", "close"))
            h, l = max(h, o, c, l), min(l, o, c, h)
            if min(o, h, l, c) > 0:
                w.writerow([r["date"], o, h, l, c, int(r["volume"])])


def prepare(codes: list[str]) -> None:
    from .backtest_data import label_market_regimes, read_rows
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "ohlcv").mkdir(parents=True)
    for code in codes:
        _convert(code, code)
    _convert("SPY", "KOSPI")  # the engine's benchmark file name
    labels = label_market_regimes(read_rows("KOSPI", WORK / "ohlcv"))
    with open(WORK / "regime_labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(labels[0]))
        w.writeheader()
        w.writerows(labels)


def replay(names: dict[str, str]):
    from . import benchmark_comparison as bc
    from .validation_backtest import BacktestEngine
    saved = os.environ.get("MIN_TRADING_VALUE")
    os.environ["MIN_TRADING_VALUE"] = MIN_TRADING_VALUE_CENTS
    try:
        engine = BacktestEngine(data_dir=WORK / "ohlcv", report_dir=WORK, score_weights=json.loads(CONFIG.read_text("utf-8"))["weights"],
                                names=names, pit_store_path=WORK / "none.sqlite3")
        sim = bc.PortfolioSimulator(engine, CAPITAL_USD * 100, 10.0, apply_live_constraints=False)
        sim.days = [d for d in sim.days if d >= START_SIGNAL_DAY]
        if not sim.days:
            return engine, sim, None
        return engine, sim, sim.run("stock_alarm", sim.build_candidate_cache(), 20260828, liquidate_at_end=False)
    finally:
        os.environ.pop("MIN_TRADING_VALUE", None) if saved is None else os.environ.__setitem__("MIN_TRADING_VALUE", saved)


def decision_rows(result) -> list[dict]:
    return [{"date": e["date"], "event": e["event"], "ticker": e["ticker"], "shares": e["shares"],
             "price_usd": f"{e['price'] / 100:.4f}", "reason": e["reason"]} for e in result.events]


def diverges(logged: list[dict], replayed: list[dict]) -> list[str]:
    """Orders already on record must be reproduced exactly (same day/event/ticker/shares/price)."""
    if not logged:
        return []
    last = max(r["date"] for r in logged)
    key = lambda r: (r["date"], r["event"], r["ticker"], str(r["shares"]), r["price_usd"])
    old, new = {key(r) for r in logged}, {key(r) for r in replayed if r["date"] <= last}
    return [f"logged but not replayed: {k}" for k in sorted(old - new)] + [f"replayed but not logged: {k}" for k in sorted(new - old)]


def _write_csv(path: Path, fields: list[str], rows: list[dict], mode: str = "w") -> None:
    exists = path.exists() and mode == "a"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def write_ledgers(engine, result) -> None:
    state = result.daily_state
    _write_csv(EQUITY, ["date", "equity_usd", "cash_usd", "invested_weight", "position_count", "return_pct", "pending"], [
        {"date": s["date"], "equity_usd": f"{s['equity'] / 100:.2f}", "cash_usd": f"{s['cash'] / 100:.2f}",
         "invested_weight": f"{s['invested_weight']:.4f}", "position_count": s["position_count"],
         "return_pct": f"{(s['equity'] / 100 / CAPITAL_USD - 1) * 100:.4f}", "pending": " ".join(json.loads(s["pending_json"]))}
        for s in state])
    _write_csv(TRADES, ["ticker", "name", "signal_date", "entry_date", "exit_date", "entry_usd", "exit_usd", "return_pct", "reason"], [
        {"ticker": t.ticker, "name": t.name, "signal_date": t.signal_date, "entry_date": t.entry_date, "exit_date": t.exit_date,
         "entry_usd": f"{t.entry_price / 100:.2f}", "exit_usd": f"{t.exit_price / 100:.2f}", "return_pct": t.return_pct,
         "reason": t.exit_reason} for t in result.trades])
    last_day, held = state[-1]["date"], json.loads(state[-1]["positions_json"])
    buys = {e["ticker"]: e for e in result.events if e["event"] == "buy"}  # latest buy per ticker (events are chronological)
    rows = []
    for ticker, shares in held.items():
        entry, mark = buys[ticker]["price"] / 100, engine.by_date[ticker][last_day][1][4] / 100 if last_day in engine.by_date[ticker] else None
        rows.append({"ticker": ticker, "name": buys[ticker]["name"], "shares": shares, "entry_date": buys[ticker]["entry_date"],
                     "entry_usd": f"{entry:.2f}", "last_usd": "" if mark is None else f"{mark:.2f}",
                     "return_pct": "" if mark is None else f"{(mark / entry - 1) * 100:.2f}"})
    _write_csv(HOLDINGS, ["ticker", "name", "shares", "entry_date", "entry_usd", "last_usd", "return_pct"], rows)


def update() -> dict:
    from .us_history_collect import refresh
    DATA.mkdir(exist_ok=True)
    if not UNIVERSE.exists():  # freeze the universe and the settings at the first run
        with open("data/backtest_us/universe.csv", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["code"] not in EXCLUDE]
        _write_csv(UNIVERSE, ["code", "name_eng", "market_value_usd"], rows)
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(current_config(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    names = {r["code"]: r["name_eng"] for r in csv.DictReader(open(UNIVERSE, encoding="utf-8"))}
    codes = list(names)
    failed = refresh(codes + ["SPY"])
    frozen, now = json.loads(CONFIG.read_text("utf-8")), current_config()
    if frozen["env"] != now["env"] or frozen["weights"] != now["weights"]:
        write_status(state="config_changed", detail="strategy settings differ from the frozen config; ledgers not updated", refresh_failures=failed)
        return {"state": "config_changed"}
    prepare(codes)
    engine, sim, result = replay(names)
    if result is None:
        write_status(state="waiting", detail=f"no trading day on/after {START_SIGNAL_DAY} yet", refresh_failures=failed)
        return {"state": "waiting"}
    replayed = decision_rows(result)
    logged = list(csv.DictReader(open(DECISIONS, encoding="utf-8"))) if DECISIONS.exists() else []
    problems = diverges(logged, replayed)
    if problems:
        write_status(state="diverged", detail=problems[:20], refresh_failures=failed)
        return {"state": "diverged", "problems": problems}
    last_logged = max((r["date"] for r in logged), default="")
    _write_csv(DECISIONS, DECISION_FIELDS, [r for r in replayed if r["date"] > last_logged], mode="a")
    write_ledgers(engine, result)
    last_day = result.daily_state[-1]["date"]
    held = json.loads(result.daily_state[-1]["positions_json"])
    stale = [t for t in held if last_day not in engine.by_date[t]]  # engine marks these at entry price
    write_status(state="ok", last_day=last_day, stale_holdings=stale, refresh_failures=failed, universe=len(codes))
    return {"state": "ok", "last_day": last_day}


if __name__ == "__main__":
    from .app import load_env
    load_env()
    print(update())
