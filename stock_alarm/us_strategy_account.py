"""Forward US strategy accounts: the backtest engine replayed from a fixed start day on a frozen US universe.

State is derived, not stored: every run refreshes the daily bars, replays the engine from the account's start day and
rewrites the ledgers. Guards keep it a record rather than a moving backtest: the strategy settings and the universe are
frozen on the first run, every order ever logged in <prefix>_decisions.csv must be reproduced by the replay, and
structurally broken bars halt the update -- in each case nothing is overwritten and the status file says why. Any
non-ok state is pushed once to the notifier. Reads/writes data/ files only; no live DB is opened for writing.

Accounts differ only in universe size and start day (same rules), so they can be compared side by side.
"""
import csv
import hashlib
from collections import Counter
import json
import os
import shutil
import io
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path("data")
SRC = DATA / "backtest_us" / "ohlcv"  # shared USD bars for every account
BACKUP_DIR = DATA / "backups" / "us"
BACKUP_KEEP_DAYS = 30

CAPITAL_USD = 100_000
EXCLUDE = {"AZN"}  # mixed listings, see data/backtest_us/QUALITY.md
MIN_TRADING_VALUE_CENTS = "5000000000"  # $50M
FROZEN_ENV = ("TOP_N", "MIN_RECOMMEND_SCORE", "VOLUME_MULTIPLIER", "MIN_MARKET_UP_RATIO", "EXECUTION_COST_BPS",
              "BACKTEST_SLIPPAGE_BPS", "MAX_MA20_DISTANCE_ATR", "SELL_RECOMMEND_COOLDOWN_DAYS",
              "MAX_DAY_CHANGE_PCT", "MAX_AVG_RANGE_PCT", "MAX_ENTRY_DAY_CHANGE_PCT", "MAX_MA20_DISTANCE_PCT",
              "TAKE_PROFIT_1_PCT", "TAKE_PROFIT_2_PCT", "TAKE_PROFIT_1_SELL_RATIO")
ENGINE_FILES = ("benchmark_comparison.py", "validation_backtest.py", "app.py", "sell_check.py",
                "backtest_data.py", "strategy_learning.py", "us_strategy_account.py")
DECISION_FIELDS = ["date", "event", "ticker", "shares", "price_usd", "reason"]
LEDGER_NAMES = ("config.json", "universe.csv", "decisions.csv", "equity.csv", "trades.csv", "holdings.csv", "status.json", "checkpoint.json")
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
    Account("100", "미국 수정 전략 (100종목)", "us_strategy_v2", "account_work_v2", "2026-10-19", None),
    Account("300", "미국 수정 전략 (300종목)", "us_strategy_300_v2", "account_work_300_v2", "2026-10-19", 300),
)


def current_config(a: Account) -> dict:
    from .strategy_learning import active_weights
    from . import us_engine
    env = {k: os.environ.get(k, "") for k in FROZEN_ENV}
    env.update({k: v for k, v in sorted(os.environ.items()) if k.startswith("SELL_")})
    engine_hashes = us_engine.hashes(ENGINE_FILES)
    return {"engine_hashes": engine_hashes, "weights": active_weights(), "env": env, "capital_usd": CAPITAL_USD, "start_signal_day": a.start_signal_day,
            "universe_size": a.universe_size, "engine_bundle_sha256":us_engine.BUNDLE_SHA256,
            "replay_contract":"confirmed-input-prefix-v2"}


def read_status(a: Account) -> dict:
    try:
        return json.loads(a.path("status.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_status(a: Account, **fields) -> None:
    """Rewrite the status; `alerted` (last pushed problem) and `bar_counts` (rows per series) carry over unless given."""
    previous = read_status(a)
    kept = {k: previous[k] for k in ("alerted", "bar_counts", "last_day") if k in previous and k not in fields}
    a.path("status.json").write_text(
        json.dumps({"updated_at": datetime.now().isoformat(timespec="seconds"), **kept, **fields}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def _convert(a: Account, code: str, out: str) -> None:
    """USD bars -> integer cents (the engine is integer based); repair bars whose high/low do not bracket open/close."""
    with open(a.work_dir / "ohlcv" / f"{out}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        source=a.work_dir/'source_ohlcv'/f'{code}.csv'
        for r in csv.DictReader(open(source if source.exists() else SRC / f"{code}.csv", encoding="utf-8")):
            o, h, l, c = (round(float(r[k]) * 100) for k in ("open", "high", "low", "close"))
            h, l = max(h, o, c, l), min(l, o, c, h)
            if min(o, h, l, c) > 0:
                w.writerow([r["date"], o, h, l, c, int(r["volume"])])


def prepare(a: Account, codes: list[str]) -> None:
    from .us_engine import module
    backtest_data=module('backtest_data')
    label_market_regimes,read_rows=backtest_data.label_market_regimes,backtest_data.read_rows
    shutil.rmtree(a.work_dir, ignore_errors=True)
    (a.work_dir / "ohlcv").mkdir(parents=True)
    source=a.work_dir/'source_ohlcv';source.mkdir()
    checkpoint=json.loads(a.path('checkpoint.json').read_text('utf-8')) if a.path('checkpoint.json').exists() else None
    archive_path=BACKUP_DIR/'inputs'/a.key/f"{checkpoint['finalized_through']}.zip" if checkpoint else None
    if checkpoint and not archive_path.exists():
        raise ValueError('Confirmed input archive missing; no historical replay changes allowed')
    with zipfile.ZipFile(archive_path) if checkpoint else nullcontext(None) as archive:
        for code in dict.fromkeys([*codes,'SPY']):
            fresh=_read_csv(SRC/f'{code}.csv')
            if archive:
                cutoff=checkpoint['finalized_through'].replace('-','')
                frozen=list(csv.DictReader(io.StringIO(archive.read('ohlcv/'+code+'.csv').decode('utf-8-sig'))))
                prefix=[r for r in frozen if r['date']<=cutoff]
                by_day={r['date']:r for r in fresh}
                if prefix and prefix[-1]['date'] in by_day:
                    prior=float(prefix[-1]['close']);current=float(by_day[prefix[-1]['date']]['close'])
                    if abs(current/prior-1)>.005:
                        raise ValueError(f'{code}: confirmed price basis changed; review required')
                fresh=prefix+[r for r in fresh if r['date']>cutoff]
            _write_csv(source/f'{code}.csv',['date','open','high','low','close','volume'],fresh)
    for code in codes:
        _convert(a, code, code)
    _convert(a, "SPY", "KOSPI")  # the engine's benchmark file name
    labels = label_market_regimes(read_rows("KOSPI", a.work_dir / "ohlcv"))
    with open(a.work_dir / "regime_labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(labels[0]))
        w.writeheader()
        w.writerows(labels)


def replay(a: Account, names: dict[str, str]):
    from .us_engine import module
    return module('us_strategy_account').replay(a,names)


def decision_rows(result) -> list[dict]:
    return [{"date": e["date"], "event": e["event"], "ticker": e["ticker"], "shares": e["shares"],
             "price_usd": f"{e['price'] / 100:.4f}", "reason": e["reason"]} for e in result.events]


def diverges(logged: list[dict], replayed: list[dict], finalized_through: str = "") -> list[str]:
    """Freeze every observed day, including no-trade days and duplicate orders."""
    last = max(finalized_through, max((r["date"] for r in logged), default=""))
    key = lambda r: tuple(str(r[k]) for k in DECISION_FIELDS)
    old = Counter(key(r) for r in logged)
    new = Counter(key(r) for r in replayed if r["date"] <= last)
    return [f"logged but not replayed: {k}" for k in sorted((old - new).elements())] + [
        f"replayed but not logged: {k}" for k in sorted((new - old).elements())]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def ledger_hashes(a: Account) -> dict:
    return {name: hashlib.sha256(a.path(name).read_bytes()).hexdigest()
            for name in ("decisions.csv", "equity.csv", "trades.csv", "holdings.csv")}


def check_finalized(a: Account, result) -> list[str]:
    """Bootstrap old accounts from equity dates; never silently rewrite old results."""
    equity = _read_csv(a.path("equity.csv"))
    checkpoint = json.loads(a.path("checkpoint.json").read_text("utf-8")) if a.path("checkpoint.json").exists() else None
    last = checkpoint["finalized_through"] if checkpoint else max((r["date"] for r in equity), default="")
    problems = diverges(_read_csv(a.path("decisions.csv")), decision_rows(result), last)
    states = [r for r in result.daily_state if r["date"] <= last]
    if checkpoint:
        if ledger_hashes(a) != checkpoint["ledger_hashes"]:
            problems.append("ledger files changed after finalization (or an interrupted write)")
        if states != checkpoint["daily_state"]:
            problems.append("finalized daily state changed")
    elif equity != [r for r in equity_rows(result) if r["date"] <= last]:
        problems.append("legacy finalized equity changed; explicit migration required")
    return problems


def migrate_legacy_engine(a: Account) -> dict:
    """Bind the legacy source-exact engine only after its confirmed replay matches."""
    from .us_engine import SOURCE_REVISION
    old=json.loads(a.path('config.json').read_text('utf-8'))
    new=current_config(a)
    if old==new:return {'account':a.key,'state':'already_bound'}
    legacy={key:new.get(key) for key in old}
    if old!=legacy or old.get('engine_bundle_sha256'):
        raise ValueError('Legacy engine/settings mismatch; automatic binding refused')
    names=read_universe(a);prepare(a,list(names))
    _engine,_sim,result=replay(a,names)
    problems=check_finalized(a,result)
    if problems:raise ValueError('Confirmed replay mismatch; binding refused: '+str(problems))
    before=ledger_hashes(a)
    stamp=datetime.now().strftime('%Y%m%d%H%M%S%f')
    folder=BACKUP_DIR/'engine_binding'/f'{a.key}-{stamp}';folder.mkdir(parents=True)
    for name in LEDGER_NAMES:
        if a.path(name).exists():shutil.copy2(a.path(name),folder/a.path(name).name)
    _atomic_json(a.path('config.json'),new)
    assert ledger_hashes(a)==before
    report={'account':a.key,'state':'bound','source_revision':SOURCE_REVISION,
            'ledger_hashes_unchanged':before,'backup':str(folder)}
    _atomic_json(folder/'binding.json',report)
    return report


def validate(codes: list[str], previous_counts: dict, today: date | None = None, expected_day=None, finalized_through=None) -> tuple[list[str], dict, list[dict]]:
    """Structural check of the downloaded bars. Returns (halting problems, rows per series, informational anomalies)."""
    today = today or date.today()
    problems, counts, anomalies = [], {}, []
    latest={};available={}
    for code in dict.fromkeys(codes + ["SPY"]):
        try:
            rows = list(csv.DictReader(open(SRC / f"{code}.csv", encoding="utf-8")))
        except OSError:
            problems.append(f"{code}: file missing")
            continue
        days = [r["date"] for r in rows]
        counts[code] = len(rows)
        latest[code]=days[-1] if days else ''
        available[code]=set(days)
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
        benchmark=latest['SPY']
        if expected_day and benchmark!=expected_day.replace('-',''):
            problems.append(f'SPY: incomplete session {benchmark}; expected {expected_day}')
        required={d for d in available['SPY'] if d>(finalized_through or benchmark).replace('-','')}
        required.add(benchmark)
        for code in codes:
            missing=required-available.get(code,set())
            if missing:problems.append(f'{code}: missing benchmark sessions {sorted(missing)}; verified halt/coverage review required')
    return problems, counts, anomalies


def _write_csv(path: Path, fields: list[str], rows: list[dict], mode: str = "w") -> None:
    if mode == "a":
        rows = _read_csv(path) + rows
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(temporary, path)


def equity_rows(result) -> list[dict]:
    return [
        {"date": s["date"], "equity_usd": f"{s['equity'] / 100:.2f}", "cash_usd": f"{s['cash'] / 100:.2f}",
         "invested_weight": f"{s['invested_weight']:.4f}", "position_count": str(s["position_count"]),
         "return_pct": f"{(s['equity'] / 100 / CAPITAL_USD - 1) * 100:.4f}", "pending": " ".join(json.loads(s["pending_json"]))}
        for s in result.daily_state]


def write_ledgers(a: Account, engine, result) -> None:
    state = result.daily_state
    rows = equity_rows(result)
    _write_csv(a.path("equity.csv"), list(rows[0]), rows)
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


def update(a: Account, failed: list | None = None, today: date | None = None, expected_day=None) -> dict:
    """Update one account. `failed` are the refresh failures of a shared download; None means download this account's codes."""
    freeze(a)
    if a.path("pending.json").exists():
        write_status(a, state="data_problem", detail="interrupted ledger commit; restore or verify before resuming")
        return {"state": "data_problem"}
    names = read_universe(a)
    codes = list(names)
    if failed is None:
        from .us_history_collect import refresh
        failed = refresh(codes + ["SPY"])
    relevant_failures = [f for f in failed if f[0] in {*codes, "SPY"}]
    if relevant_failures:
        write_status(a, state="data_problem", detail="refresh incomplete; no day finalized", refresh_failures=relevant_failures)
        return {"state": "data_problem"}
    frozen, now = json.loads(a.path("config.json").read_text("utf-8")), current_config(a)
    if frozen != now:
        write_status(a, state="config_changed", detail="strategy settings differ from the frozen config; ledgers not updated", refresh_failures=failed)
        return {"state": "config_changed"}
    checkpoint=json.loads(a.path('checkpoint.json').read_text('utf-8')) if a.path('checkpoint.json').exists() else {}
    problems, counts, anomalies = validate(codes, read_status(a).get("bar_counts", {}), today,expected_day,checkpoint.get('finalized_through') or (date.fromisoformat(a.start_signal_day)-timedelta(days=1)).isoformat())
    if problems:
        write_status(a, state="data_problem", detail=problems[:20], refresh_failures=failed)
        return {"state": "data_problem", "problems": problems}
    prepare(a, codes)
    engine, sim, result = replay(a, names)
    if result is None:
        write_status(a, state="waiting", detail=f"no trading day on/after {a.start_signal_day} yet", refresh_failures=failed, bar_counts=counts)
        return {"state": "waiting"}
    diverged = check_finalized(a, result)
    if diverged:
        write_status(a, state="diverged", detail=diverged[:20], refresh_failures=failed)
        return {"state": "diverged", "problems": diverged}
    last_day = result.daily_state[-1]["date"]
    stale = sorted({t for s in result.daily_state for t in json.loads(s["positions_json"])
                    if s["date"] not in engine.by_date[t]})
    if stale:
        write_status(a, state="data_problem", detail="missing holding prices; ledgers not updated", stale_holdings=stale)
        return {"state": "data_problem"}
    # Mark even the first commit, which has no previous checkpoint to check.
    _atomic_json(a.path("pending.json"), {"finalizing_through": last_day})
    _write_csv(a.path("decisions.csv"), DECISION_FIELDS, decision_rows(result))
    write_ledgers(a, engine, result)
    # Commit marker last: a partial multi-file write fails the old hashes next run.
    _atomic_json(a.path("checkpoint.json"), {"finalized_through": last_day,
                 "daily_state": result.daily_state, "ledger_hashes": ledger_hashes(a)})
    a.path("pending.json").unlink()
    write_status(a, state="ok", last_day=last_day, stale_holdings=stale, refresh_failures=failed, universe=len(codes),
                 anomalies=anomalies, bar_counts=counts)
    from .us_account_audit import snapshot
    snapshot(a)
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


def update_all(notify=None,expected_day=None) -> dict:
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
            options={'expected_day':expected_day} if expected_day else {}
            results[a.key] = update(a, failed,**options) if a.path("universe.csv").exists() else {"state": "error"}
        except Exception as error:
            write_status(a, state="error", detail=f"{type(error).__name__}: {error}")
            results[a.key] = {"state": "error"}
        alert_if_needed(a, results[a.key]["state"], notify)
    return results


if __name__ == "__main__":
    from .app import load_env
    load_env()
    print(update_all())
