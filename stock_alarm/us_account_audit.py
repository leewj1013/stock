"""Read-only evidence for frozen US strategy account updates."""

import csv
import io
import json
import os
import zipfile
from datetime import date

from . import us_strategy_account as account

INPUT_SNAPSHOTS = 7


def _write_inputs(a: account.Account, target, manifest: dict) -> None:
    if target.exists():
        return
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            for ticker in dict.fromkeys([*account.read_universe(a), "SPY"]):
                archive.write(account.SRC / f"{ticker}.csv", f"ohlcv/{ticker}.csv")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot(a: account.Account) -> str:
    """Keep the exact downloaded bars used for a successful finalized account day."""
    checkpoint = json.loads(a.path("checkpoint.json").read_text(encoding="utf-8"))
    day = checkpoint["finalized_through"]
    if account.read_status(a).get("state") != "ok" or account.ledger_hashes(a) != checkpoint["ledger_hashes"]:
        raise ValueError("account is not a verified finalized state")
    folder = account.BACKUP_DIR / "inputs" / a.key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{day}.zip"
    _write_inputs(a, target, {"account": a.key, "finalized_through": day,
                              "ledger_hashes": checkpoint["ledger_hashes"]})
    for old in sorted(folder.glob("*.zip"))[:-INPUT_SNAPSHOTS]:
        old.unlink()
    return str(target)


def _changed_bars(a: account.Account, day: str) -> dict:
    source = account.BACKUP_DIR / "inputs" / a.key / f"{day}.zip"
    if not source.exists():
        return {"snapshot_available": False, "changes": []}
    changes = []
    with zipfile.ZipFile(source) as archive:
        for ticker in dict.fromkeys([*account.read_universe(a), "SPY"]):
            name = f"ohlcv/{ticker}.csv"
            old = {r["date"]: r for r in csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))) if r["date"] <= day.replace("-", "")}
            new = {r["date"]: r for r in account._read_csv(account.SRC / f"{ticker}.csv") if r["date"] <= day.replace("-", "")}
            for changed_day in sorted(old.keys() | new.keys()):
                if old.get(changed_day) != new.get(changed_day):
                    changes.append({"ticker": ticker, "date": changed_day, "saved": old.get(changed_day), "current": new.get(changed_day)})
    return {"snapshot_available": True, "changes": changes}


def diagnose(a: account.Account) -> dict:
    """Compare a halted replay to its checkpoint without changing any ledger."""
    checkpoint = json.loads(a.path("checkpoint.json").read_text(encoding="utf-8"))
    day = checkpoint["finalized_through"]
    engine, _sim, result = account.replay(a, account.read_universe(a))
    saved = {row["date"]: row for row in checkpoint["daily_state"]}
    current = {row["date"]: row for row in result.daily_state if row["date"] <= day}
    differences = []
    for changed_day in sorted(saved.keys() | current.keys()):
        before, after = saved.get(changed_day, {}), current.get(changed_day, {})
        fields = {key: {"saved": before.get(key), "current": after.get(key)}
                  for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        if fields:
            differences.append({"date": changed_day, "fields": fields})
    scores = {}
    for difference in differences:
        pending = difference["fields"].get("pending_json")
        if not pending:
            continue
        tickers = set(json.loads(pending["saved"] or "[]")) | set(json.loads(pending["current"] or "[]"))
        scores[difference["date"]] = {item.pick.ticker: item.pick.score for item in engine.passed_evaluations(difference["date"])
                                      if item.pick and item.pick.ticker in tickers}
    report = {"account": a.key, "finalized_through": day, "ledger_hashes_match": account.ledger_hashes(a) == checkpoint["ledger_hashes"],
              "order_differences": account.diverges(account._read_csv(a.path("decisions.csv")), account.decision_rows(result), day),
              "daily_state_differences": differences, "current_candidate_scores": scores,
              "input_bars": _changed_bars(a, day)}
    target = account.BACKUP_DIR / "diagnostics" / f"{a.prefix}-{date.today():%Y%m%d}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    current_inputs = target.with_suffix(".current-inputs.zip")
    _write_inputs(a, current_inputs, {"account": a.key, "captured_at": date.today().isoformat(),
                                      "kind": "diverged_replay_inputs", "finalized_through": day})
    for old in sorted(target.parent.glob(f"{a.prefix}-*.current-inputs.zip"))[:-INPUT_SNAPSHOTS]:
        old.unlink()
    report["current_input_archive"] = str(current_inputs)
    account._atomic_json(target, report)
    report["report_path"] = str(target)
    return report
