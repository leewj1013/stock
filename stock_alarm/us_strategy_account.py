"""Forward US strategy accounts: the backtest engine replayed from a fixed start day on a frozen US universe.

State is derived, not stored: every run refreshes the daily bars, replays the engine from the account's start day and
rewrites the ledgers. Guards keep it a record rather than a moving backtest: the strategy settings and the universe are
frozen on the first run, every order ever logged in <prefix>_decisions.csv must be reproduced by the replay, and
structurally broken bars halt the update -- in each case nothing is overwritten and the status file says why. Any
non-ok state is pushed once to the notifier. Reads/writes data/ files only; no live DB is opened for writing.

Accounts differ only in universe size and start day (same rules), so they can be compared side by side.
"""
import csv
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DATA = Path("data")
SRC = DATA / "backtest_us" / "ohlcv"  # shared USD bars for every account
BACKUP_DIR = DATA / "backups" / "us"
BACKUP_KEEP_DAYS = 30

CAPITAL_USD = 100_000
EXCLUDE = {"AZN"}  # mixed listings, see data/backtest_us/QUALITY.md
MIN_TRADING_VALUE_CENTS = "5000000000"  # $50M
FROZEN_ENV = ("TOP_N", "MIN_RECOMMEND_SCORE", "VOLUME_MULTIPLIER", "MIN_MARKET_UP_RATIO", "EXECUTION_COST_BPS",
              "BACKTEST_SLIPPAGE_BPS", "MAX_MA20_DISTANCE_ATR", "SELL_RECOMMEND_COOLDOWN_DAYS")
DECISION_FIELDS = ["date", "event", "ticker", "shares", "price_usd", "reason"]
LEDGER_NAMES = ("config.json", "universe.csv", "decisions.csv", "equity.csv", "trades.csv", "holdings.csv", "status.json")
SPY_STALE_DAYS = 7  # a long US holiday stretch is 4 days
ANOMALY_MOVE = 0.40  # |daily move| on the newest sessions that is reported (not halted: real earnings gaps exist)
ALERT_TEXT = {"diverged": "기록된 주문을 재현하지 못해 갱신을 중단했습니다",
              "config_changed": "전략 설정이 고정값과 달라 갱신을 중단했습니다",
              "data_problem": "수집 데이터에 구조적 이상이 있어 갱신을 중단했습니다",
              "error": "갱신 중 오류가 나서 중단했습니다"}


@dataclass(frozen=True)
class Account:
    key: str
    label: str
    prefix: str  # files: data/<prefix>_equity.csv, ...
    work: str  # integer-cent copies the engine reads, plus regime labels
    start_signal_day: str  # first signal made at this close, first fill at the next open
    universe_size: int | None  # None: freeze the existing backtest universe; else the current top-N by market cap

    def path(self, name: str) -> Path:
        return DATA / f"{self.prefix}_{name}"

    @property
    def work_dir(self) -> Path:
        return DATA / "backtest_us" / self.work


ACCOUNTS = (
    Account("100", "미국 전략 (100종목)", "us_strategy", "account_work", "2026-10-06", None),
    Account("300", "미국 전략 (300종목)", "us_strategy_300", "account_work_300", "2026-10-14", 300),
)


def current_config(a: Account) -> dict:
    from .strategy_learning import active_weights
    env = {k: os.environ.get(k, "") for k in FROZEN_ENV}
    env.update({k: v for k, v in sorted(os.environ.items()) if k.startswith("SELL_")})
    return {"weights": active_weights(), "env": env, "capital_usd": CAPITAL_USD, "start_signal_day": a.start_signal_day,
            "universe_size": a.universe_size}


def read_status(a: Account) -> dict:
    try:
        return json.loads(a.path("status.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_status(a: Account, **fields) -> None:
    """Rewrite the status; `alerted` (last pushed problem) and `bar_counts` (rows per series) carry over unless given."""
    previous = read_status(a)
    kept = {k: previous[k] for k in ("alerted", "bar_counts") if k in previous and k not in fields}
    a.path("status.json").write_text(
        json.dumps({"updated_at": datetime.now().isoformat(timespec="seconds"), **kept, **fields}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def _convert(a: Account, code: str, out: str) -> None:
    """USD bars -> integer cents (the engine is integer based); repair bars whose high/low do not bracket open/close."""
    with open(a.work_dir / "ohlcv" / f"{out}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for r in csv.DictReader(open(SRC / f"{code}.csv", encoding="utf-8")):
            o, h, l, c = (round(float(r[k]) * 100) for k in ("open", "high", "low", "close"))
            h, l = max(h, o, c, l), min(l, o, c, h)
            if min(o, h, l, c) > 0:
                w.writerow([r["date"], o, h, l, c, int(r["volume"])])


def prepare(a: Account, codes: list[str]) -> None:
    from .backtest_data import label_market_regimes, read_rows
    shutil.rmtree(a.work_dir, ignore_errors=True)
    (a.work_dir / "ohlcv").mkdir(parents=True)
    for code in codes:
        _convert(a, code, code)
    _convert(a, "SPY", "KOSPI")  # the engine's benchmark file name
    labels = label_market_regimes(read_rows("KOSPI", a.work_dir / "ohlcv"))
    with open(a.work_dir / "regime_labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(labels[0]))
        w.writeheader()
        w.writerows(labels)


def replay(a: Account, names: dict[str, str]):
    from . import benchmark_comparison as bc
    from .validation_backtest import BacktestEngine
    saved = os.environ.get("MIN_TRADING_VALUE")
    os.environ["MIN_TRADING_VALUE"] = MIN_TRADING_VALUE_CENTS
    try:
        engine = BacktestEngine(data_dir=a.work_dir / "ohlcv", report_dir=a.work_dir,
                                score_weights=json.loads(a.path("config.json").read_text("utf-8"))["weights"],
                                names=names, pit_store_path=a.work_dir / "none.sqlite3")
        sim = bc.PortfolioSimulator(engine, CAPITAL_USD * 100, 10.0, apply_live_constraints=False)
        sim.days = [d for d in sim.days if d >= a.start_signal_day]
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


def validate(codes: list[str], previous_counts: dict, today: date | None = None) -> tuple[list[str], dict, list[dict]]:
    """Structural check of the downloaded bars. Returns (halting problems, rows per series, informational anomalies)."""
    today = today or date.today()
    problems, counts, anomalies = [], {}, []
    for code in dict.fromkeys(codes + ["SPY"]):
        try:
            rows = list(csv.DictReader(open(SRC / f"{code}.csv", encoding="utf-8")))
        except OSError:
            problems.append(f"{code}: file missing")
            continue
        days = [r["date"] for r in rows]
        counts[code] = len(rows)
        if not rows:
            problems.append(f"{code}: no rows")
        elif days != sorted(set(days)):
            problems.append(f"{code}: duplicate or unsorted dates")
        elif any(float(r["close"]) <= 0 for r in rows):
            problems.append(f"{code}: non-positive close")
        elif counts[code] < int(previous_counts.get(code, 0)):
            problems.append(f"{code}: history shrank {previous_counts[code]} -> {counts[code]}")
        else:
            closes = [float(r["close"]) for r in rows[-4:]]
            for i in range(1, len(closes)):
                move = closes[i] / closes[i - 1] - 1
                if abs(move) > ANOMALY_MOVE:
                    anomalies.append({"ticker": code, "date": rows[-4:][i]["date"], "move_pct": round(move * 100, 1)})
    if "SPY" in counts and counts["SPY"]:
        last = datetime.strptime(list(csv.DictReader(open(SRC / "SPY.csv", encoding="utf-8")))[-1]["date"], "%Y%m%d").date()
        if (today - last).days > SPY_STALE_DAYS:
            problems.append(f"SPY: newest bar {last} is older than {SPY_STALE_DAYS} days")
    return problems, counts, anomalies


def _write_csv(path: Path, fields: list[str], rows: list[dict], mode: str = "w") -> None:
    exists = path.exists() and mode == "a"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def write_ledgers(a: Account, engine, result) -> None:
    state = result.daily_state
    _write_csv(a.path("equity.csv"), ["date", "equity_usd", "cash_usd", "invested_weight", "position_count", "return_pct", "pending"], [
        {"date": s["date"], "equity_usd": f"{s['equity'] / 100:.2f}", "cash_usd": f"{s['cash'] / 100:.2f}",
         "invested_weight": f"{s['invested_weight']:.4f}", "position_count": s["position_count"],
         "return_pct": f"{(s['equity'] / 100 / CAPITAL_USD - 1) * 100:.4f}", "pending": " ".join(json.loads(s["pending_json"]))}
        for s in state])
    _write_csv(a.path("trades.csv"), ["ticker", "name", "signal_date", "entry_date", "exit_date", "entry_usd", "exit_usd", "return_pct", "reason"], [
        {"ticker": t.ticker, "name": t.name, "signal_date": t.signal_date, "entry_date": t.entry_date, "exit_date": t.exit_date,
         "entry_usd": f"{t.entry_price / 100:.2f}", "exit_usd": f"{t.exit_price / 100:.2f}", "return_pct": t.return_pct,
         "reason": t.exit_reason} for t in result.trades])
    last_day, held = state[-1]["date"], json.loads(state[-1]["positions_json"])
    buys = {e["ticker"]: e for e in result.events if e["event"] == "buy"}  # latest buy per ticker (events are chronological)
    rows = []
    for ticker, shares in held.items():
        entry = buys[ticker]["price"] / 100
        mark = engine.by_date[ticker][last_day][1][4] / 100 if last_day in engine.by_date[ticker] else None
        rows.append({"ticker": ticker, "name": buys[ticker]["name"], "shares": shares, "entry_date": buys[ticker]["entry_date"],
                     "entry_usd": f"{entry:.2f}", "last_usd": "" if mark is None else f"{mark:.2f}",
                     "return_pct": "" if mark is None else f"{(mark / entry - 1) * 100:.2f}"})
    _write_csv(a.path("holdings.csv"), ["ticker", "name", "shares", "entry_date", "entry_usd", "last_usd", "return_pct"], rows)


def backup(a: Account, root: Path | None = None, today: date | None = None) -> Path:
    """Copy the account's ledgers into <root>/<yyyymmdd>/ and drop day folders older than BACKUP_KEEP_DAYS (data/ is not in git)."""
    root, today = Path(root or BACKUP_DIR), today or date.today()
    target = root / f"{today:%Y%m%d}"
    target.mkdir(parents=True, exist_ok=True)
    for name in LEDGER_NAMES:
        if a.path(name).exists():
            shutil.copy2(a.path(name), target / a.path(name).name)
    for old in sorted(p for p in root.iterdir() if p.is_dir() and p.name.isdigit())[:-BACKUP_KEEP_DAYS]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def freeze(a: Account) -> None:
    """On the first run, freeze the universe and the strategy settings."""
    DATA.mkdir(exist_ok=True)
    if not a.path("universe.csv").exists():
        if a.universe_size is None:
            with open(DATA / "backtest_us" / "universe.csv", encoding="utf-8") as f:
                rows = [r for r in csv.DictReader(f) if r["code"] not in EXCLUDE]
        else:
            from .us_history_collect import top_by_market_cap
            rows = [{"code": s["reutersCode"], "name_eng": s["stockNameEng"], "market_value_usd": s["marketValueRaw"]}
                    for s in top_by_market_cap(a.universe_size) if s["reutersCode"] not in EXCLUDE]
        _write_csv(a.path("universe.csv"), ["code", "name_eng", "market_value_usd"], rows)
    if not a.path("config.json").exists():
        a.path("config.json").write_text(json.dumps(current_config(a), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def read_universe(a: Account) -> dict[str, str]:
    return {r["code"]: r["name_eng"] for r in csv.DictReader(open(a.path("universe.csv"), encoding="utf-8"))}


def update(a: Account, failed: list | None = None, today: date | None = None) -> dict:
    """Update one account. `failed` are the refresh failures of a shared download; None means download this account's codes."""
    freeze(a)
    names = read_universe(a)
    codes = list(names)
    if failed is None:
        from .us_history_collect import refresh
        failed = refresh(codes + ["SPY"])
    frozen, now = json.loads(a.path("config.json").read_text("utf-8")), current_config(a)
    if frozen["env"] != now["env"] or frozen["weights"] != now["weights"]:
        write_status(a, state="config_changed", detail="strategy settings differ from the frozen config; ledgers not updated", refresh_failures=failed)
        return {"state": "config_changed"}
    problems, counts, anomalies = validate(codes, read_status(a).get("bar_counts", {}), today)
    if problems:
        write_status(a, state="data_problem", detail=problems[:20], refresh_failures=failed)
        return {"state": "data_problem", "problems": problems}
    prepare(a, codes)
    engine, sim, result = replay(a, names)
    if result is None:
        write_status(a, state="waiting", detail=f"no trading day on/after {a.start_signal_day} yet", refresh_failures=failed, bar_counts=counts)
        return {"state": "waiting"}
    replayed = decision_rows(result)
    logged = list(csv.DictReader(open(a.path("decisions.csv"), encoding="utf-8"))) if a.path("decisions.csv").exists() else []
    diverged = diverges(logged, replayed)
    if diverged:
        write_status(a, state="diverged", detail=diverged[:20], refresh_failures=failed)
        return {"state": "diverged", "problems": diverged}
    last_logged = max((r["date"] for r in logged), default="")
    _write_csv(a.path("decisions.csv"), DECISION_FIELDS, [r for r in replayed if r["date"] > last_logged], mode="a")
    write_ledgers(a, engine, result)
    last_day = result.daily_state[-1]["date"]
    held = json.loads(result.daily_state[-1]["positions_json"])
    stale = [t for t in held if last_day not in engine.by_date[t]]  # engine marks these at entry price
    write_status(a, state="ok", last_day=last_day, stale_holdings=stale, refresh_failures=failed, universe=len(codes),
                 anomalies=anomalies, bar_counts=counts)
    backup(a)
    return {"state": "ok", "last_day": last_day}


def set_alerted(a: Account, value: str) -> None:
    status = read_status(a)
    status["alerted"] = value
    a.path("status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def alert_if_needed(a: Account, state: str, notify) -> bool:
    """Push a non-ok state once; clear the memory when the account is ok again. Returns True if a message was sent."""
    previous = read_status(a).get("alerted", "")
    if state in ("ok", "waiting"):
        if previous:
            set_alerted(a, "")
        return False
    if previous == state:
        return False
    notify(f"미국 계좌 경고 · {a.label}: {ALERT_TEXT.get(state, state)}. 대시보드 /us 의 '데이터 상태'를 확인하세요.")
    set_alerted(a, state)
    return True


def update_all(notify=None) -> dict:
    """Refresh the bars once for every account, update each account independently, then alert on problems."""
    from .us_history_collect import refresh
    for a in ACCOUNTS:
        try:
            freeze(a)
        except Exception as error:  # e.g. the market-cap list is unreachable; retried on the next run
            write_status(a, state="error", detail=f"freeze failed: {error}")
    codes = sorted({c for a in ACCOUNTS if a.path("universe.csv").exists() for c in read_universe(a)})
    failed = refresh(codes + ["SPY"])
    if notify is None:
        from .notifier import send_notification
        notify = lambda text: send_notification(text, event_type="us_account")
    results = {}
    for a in ACCOUNTS:
        try:
            results[a.key] = update(a, failed) if a.path("universe.csv").exists() else {"state": "error"}
        except Exception as error:
            write_status(a, state="error", detail=f"{type(error).__name__}: {error}")
            results[a.key] = {"state": "error"}
        alert_if_needed(a, results[a.key]["state"], notify)
    return results


if __name__ == "__main__":
    from .app import load_env
    load_env()
    print(update_all())
