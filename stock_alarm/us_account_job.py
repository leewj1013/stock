"""Independent post-US-close SPY and forward strategy account update."""

import csv
import json
import sys
from datetime import date, datetime

from .app import load_env
from .us_strategy_account import ACCOUNTS, current_config, read_status


class AccountUpdateError(RuntimeError):
    """The account-level warning has already been handled."""


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
    warnings = []
    results = update_all(notify=warnings.append)
    from .notifier import send_notification
    from .us_account_audit import diagnose, snapshot

    reports = {}
    audit_errors = {}
    for account in ACCOUNTS:
        state = results.get(account.key, {}).get("state")
        if state == "ok":
            try:
                snapshot(account)
            except Exception as error:
                audit_errors[account.key] = f"{type(error).__name__}: {error}"
        elif state == "diverged":
            try:
                reports[account.key] = diagnose(account)
            except Exception as error:
                reports[account.key] = {"diagnostic_error": f"{type(error).__name__}: {error}"}
    for warning in warnings:
        if "기록된 주문을 재현하지 못해" in warning:
            warning = warning.replace("기록된 주문을 재현하지 못해", "확정된 계좌 상태가 현재 자료의 재계산과 달라")
            for account in ACCOUNTS:
                if account.label in warning and account.key in reports:
                    differences = reports[account.key].get("daily_state_differences", [])
                    if differences:
                        first = differences[0]
                        pending = first["fields"].get("pending_json")
                        if pending:
                            warning += f" 차이: {first['date']} 매수 대기 {pending['saved']} → {pending['current']}."
                    break
        send_notification(warning, event_type="us_account")
    if audit_errors:
        send_notification(f"미국 계좌 입력 보관 실패: {audit_errors}. logs/us_account.err.log를 확인하세요.", event_type="us_account")
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
    if audit_errors or any(result.get("state") not in ("ok", "waiting") for result in results.values()):
        raise AccountUpdateError(f"US strategy update failed: {results}; diagnostics: {reports}; audit_errors: {audit_errors}")
    return {"spy_days": spy_days, "accounts": results, "korean_names_added": enriched, "research": research}


def main() -> None:
    load_env()
    if "--check" in sys.argv[1:]:
        states = preflight()
        print(json.dumps(states, ensure_ascii=False))
        if any(state == "config_changed" for state in states.values()):
            raise SystemExit(1)
        return
    try:
        print(json.dumps(run(retry="--retry" in sys.argv[1:]), ensure_ascii=False))
    except AccountUpdateError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
