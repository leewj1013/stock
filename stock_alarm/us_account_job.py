"""Independent post-US-close SPY and forward strategy account update."""

import csv
import json
import sys
from datetime import date, datetime

from .app import load_env
from .us_strategy_account import ACCOUNTS, current_config, read_status


def preflight() -> dict[str, str]:
    """Read-only check of the frozen trading settings before any account writes."""
    states = {}
    for account in ACCOUNTS:
        path = account.path("config.json")
        if not path.exists():
            states[account.key] = "not_frozen"
            continue
        frozen = json.loads(path.read_text(encoding="utf-8"))
        current = current_config(account)
        states[account.key] = "ready" if frozen == current else "config_changed"
    return states


def run(*, retry: bool = False, today: date | None = None) -> dict:
    load_env()
    today = today or date.today()
    states = preflight()
    if any(state == "config_changed" for state in states.values()):
        raise RuntimeError(f"US strategy frozen settings differ: {states}")

    from .us_spy_hold import update as update_spy
    from .us_strategy_account import update_all
    from .us_spy_hold import LEDGER_PATH

    spy_days = update_spy()
    with open(LEDGER_PATH, encoding="utf-8", newline="") as file:
        latest = list(csv.DictReader(file))[-1]["date"]
    latest_date = datetime.strptime(latest, "%Y%m%d").date()
    due_accounts = [account for account in ACCOUNTS if today > date.fromisoformat(account.start_signal_day)]
    if due_accounts and any(latest_date < date.fromisoformat(account.start_signal_day) for account in due_accounts):
        raise RuntimeError(f"US first-session SPY data is missing: newest={latest_date}")
    if retry:
        latest_day = latest_date.isoformat()
        statuses = [read_status(account) for account in ACCOUNTS]
        if all(status.get("state") == "ok" and status.get("last_day") == latest_day for status in statuses):
            return {"spy_days": spy_days, "accounts": "already_current", "korean_names_added": 0}
    results = update_all()
    if any(results.get(account.key, {}).get("state") == "waiting" and
           latest_date >= date.fromisoformat(account.start_signal_day) for account in due_accounts):
        raise RuntimeError(f"US first-session strategy ledger is still waiting: {results}")
    try:
        from .us_name_metadata import enrich_universe_names

        enriched = enrich_universe_names()
    except Exception:
        enriched = 0  # Display names do not affect trading ledgers.
    try:
        from .us_research import run as run_research
        research = run_research()
    except Exception as error:
        research = {"error": f"{type(error).__name__}: {error}"}  # Observation must never stop the frozen accounts.
    if any(result.get("state") not in ("ok", "waiting") for result in results.values()):
        raise RuntimeError(f"US strategy update failed: {results}")
    return {"spy_days": spy_days, "accounts": results, "korean_names_added": enriched, "research": research}


def main() -> None:
    load_env()
    if "--check" in sys.argv[1:]:
        states = preflight()
        print(json.dumps(states, ensure_ascii=False))
        if any(state == "config_changed" for state in states.values()):
            raise SystemExit(1)
        return
    print(json.dumps(run(retry="--retry" in sys.argv[1:]), ensure_ascii=False))


if __name__ == "__main__":
    main()
