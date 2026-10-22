"""One-time, guarded reconciliation of the 2026-10-23 US account divergence."""

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from stock_alarm.app import load_env
from stock_alarm import us_strategy_account as account
from stock_alarm.us_account_audit import snapshot


OLD_LEARNING_HASH = "36940eb9b089f77c1c6d96fd2e5f275a397a14823a4bd6d1f16491b08633d47e"
NEW_LEARNING_HASH = "f5f28968049a456aa08434967074b7fac1b277eca5e0e0046c1a94dfd265ef0b"
OLD_PENDING = '["CSX.O", "ISRG.O", "TXN.O"]'
NEW_PENDING = '["HON.O", "ISRG.O", "TXN.O"]'
REVIEWED_INPUTS = account.BACKUP_DIR / "diagnostics" / "us_strategy_300_v2-20261023.current-inputs.zip"


def run(*, apply: bool = False) -> Path | None:
    load_env()
    accounts = {a.key: a for a in account.ACCOUNTS}
    assert set(accounts) == {"100", "300"}
    assert REVIEWED_INPUTS.exists(), "reviewed input archive is missing"
    with zipfile.ZipFile(REVIEWED_INPUTS) as archive:
        for ticker in dict.fromkeys([*account.read_universe(accounts["300"]), "SPY"]):
            name = f"ohlcv/{ticker}.csv"
            assert hashlib.sha256(archive.read(name)).digest() == hashlib.sha256((account.SRC / f"{ticker}.csv").read_bytes()).digest(), name

    replays = {}
    for key, a in accounts.items():
        frozen = json.loads(a.path("config.json").read_text(encoding="utf-8"))
        current = account.current_config(a)
        assert frozen["engine_hashes"]["strategy_learning.py"] == OLD_LEARNING_HASH
        assert current["engine_hashes"]["strategy_learning.py"] == NEW_LEARNING_HASH
        frozen["engine_hashes"]["strategy_learning.py"] = NEW_LEARNING_HASH
        assert frozen == current, f"unexpected config change: {key}"
        checkpoint = json.loads(a.path("checkpoint.json").read_text(encoding="utf-8"))
        assert account.ledger_hashes(a) == checkpoint["ledger_hashes"], f"ledger changed: {key}"
        engine, _sim, result = account.replay(a, account.read_universe(a))
        assert result.daily_state[-1]["date"] == "2026-10-22", f"unexpected last day: {key}"
        assert account.diverges(account._read_csv(a.path("decisions.csv")), account.decision_rows(result), checkpoint["finalized_through"]) == []
        saved = {r["date"]: r for r in checkpoint["daily_state"]}
        observed = {r["date"]: r for r in result.daily_state if r["date"] <= checkpoint["finalized_through"]}
        if key == "100":
            assert checkpoint["finalized_through"] == "2026-10-22"
            assert saved == observed
            assert account.read_status(a)["state"] == "ok"
        else:
            assert checkpoint["finalized_through"] == "2026-10-21"
            assert account.read_status(a)["state"] == "diverged"
            assert saved.keys() == observed.keys()
            changes = [(day, field, old[field], observed[day][field]) for day, old in saved.items()
                       for field in old if old[field] != observed[day][field]]
            assert changes == [("2026-10-21", "pending_json", OLD_PENDING, NEW_PENDING)], changes
        replays[key] = (a, engine, result)

    if not apply:
        return None

    backup_dir = account.BACKUP_DIR / "migrations" / datetime.now().strftime("20261023-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=False)
    for a, _engine, _result in replays.values():
        for name in account.LEDGER_NAMES:
            shutil.copy2(a.path(name), backup_dir / a.path(name).name)
    shutil.copy2(REVIEWED_INPUTS, backup_dir / REVIEWED_INPUTS.name)
    (backup_dir / "reason.json").write_text(json.dumps({"reason": "2026-10-21 pending order differs on current input; original input unavailable",
                                                  "saved_pending": OLD_PENDING, "replayed_pending": NEW_PENDING,
                                                  "old_learning_hash": OLD_LEARNING_HASH, "new_learning_hash": NEW_LEARNING_HASH}, indent=2), encoding="utf-8")

    a, engine, result = replays["300"]
    account._atomic_json(a.path("pending.json"), {"migration_backup": str(backup_dir)})
    account._write_csv(a.path("decisions.csv"), account.DECISION_FIELDS, account.decision_rows(result))
    account.write_ledgers(a, engine, result)
    account._atomic_json(a.path("checkpoint.json"), {"finalized_through": "2026-10-22",
                                                     "daily_state": result.daily_state, "ledger_hashes": account.ledger_hashes(a)})
    for key, (candidate, _engine, _result) in replays.items():
        account._atomic_json(candidate.path("config.json"), account.current_config(candidate))
    assert account.check_finalized(a, result) == []
    assert all(json.loads(candidate.path("config.json").read_text(encoding="utf-8")) == account.current_config(candidate)
               for candidate, _engine, _result in replays.values())
    a.path("pending.json").unlink()
    account.write_status(a, state="ok", last_day="2026-10-22", alerted="", migration_backup=str(backup_dir))
    for candidate, _engine, _result in replays.values():
        account.backup(candidate)
        snapshot(candidate)
    return backup_dir


if __name__ == "__main__":
    try:
        result = run(apply="--apply" in sys.argv[1:])
        print(result or "All migration preconditions passed; no files changed. Use --apply to reconcile.")
    except Exception as error:
        print(f"Migration stopped: {type(error).__name__}: {error}", file=sys.stderr)
        raise
