import csv
import json
from datetime import date
from pathlib import Path

import pytest

from stock_alarm import us_account_job as job
from stock_alarm import us_spy_hold, us_strategy_account as strategy


def test_preflight_rejects_changed_frozen_settings_before_account_update(tmp_path, monkeypatch):
    account = strategy.Account("test", "test", "test", "work", "2026-10-19", None)
    monkeypatch.setattr(strategy, "DATA", tmp_path)
    monkeypatch.setattr(job, "ACCOUNTS", (account,))
    monkeypatch.setattr(job, "current_config", lambda _: {"rule": "new"})
    account.path("config.json").write_text(json.dumps({"rule": "old"}), encoding="utf-8")
    monkeypatch.setattr(job, "load_env", lambda: None)
    monkeypatch.setattr(us_spy_hold, "update", lambda: pytest.fail("SPY ledger must not update"))

    assert job.preflight() == {"test": "config_changed"}
    with pytest.raises(RuntimeError, match="frozen settings differ"):
        job.run()


def test_retry_skips_replay_when_both_accounts_match_latest_spy_day(tmp_path, monkeypatch):
    accounts = tuple(strategy.Account(key, key, key, "work", "2026-10-19", None) for key in ("100", "300"))
    monkeypatch.setattr(strategy, "DATA", tmp_path)
    monkeypatch.setattr(job, "ACCOUNTS", accounts)
    monkeypatch.setattr(job, "load_env", lambda: None)
    monkeypatch.setattr(job, "current_config", lambda _: {"rule": "same"})
    for account in accounts:
        account.path("config.json").write_text(json.dumps({"rule": "same"}), encoding="utf-8")
        account.path("status.json").write_text(json.dumps({"state": "ok", "last_day": "2026-10-19"}), encoding="utf-8")
    ledger = tmp_path / "spy.csv"
    with ledger.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date"])
        writer.writerow(["20261019"])
    monkeypatch.setattr(us_spy_hold, "LEDGER_PATH", str(ledger))
    monkeypatch.setattr(us_spy_hold, "update", lambda: 1)
    monkeypatch.setattr(strategy, "update_all", lambda: pytest.fail("Replay must not repeat"))

    assert job.run(retry=True, today=date(2026, 10, 20))["accounts"] == "already_current"


def test_first_session_requires_new_spy_data_before_replay(tmp_path, monkeypatch):
    account = strategy.Account("100", "100", "100", "work", "2026-10-19", None)
    monkeypatch.setattr(strategy, "DATA", tmp_path)
    monkeypatch.setattr(job, "ACCOUNTS", (account,))
    monkeypatch.setattr(job, "load_env", lambda: None)
    monkeypatch.setattr(job, "current_config", lambda _: {"rule": "same"})
    account.path("config.json").write_text(json.dumps({"rule": "same"}), encoding="utf-8")
    ledger = tmp_path / "spy.csv"
    with ledger.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date"])
        writer.writerow(["20261016"])
    monkeypatch.setattr(us_spy_hold, "LEDGER_PATH", str(ledger))
    monkeypatch.setattr(us_spy_hold, "update", lambda: 1)
    monkeypatch.setattr(strategy, "update_all", lambda: pytest.fail("Replay must wait for the new SPY day"))

    with pytest.raises(RuntimeError, match="first-session SPY data is missing"):
        job.run(today=date(2026, 10, 20))


def test_frozen_strategy_module_stays_unchanged_for_display_name_metadata():
    # A change here would cause both pre-registered strategy accounts to halt.
    assert "us_name_metadata.py" not in strategy.ENGINE_FILES
    assert "enrich_universe_names" not in Path(strategy.__file__).read_text(encoding="utf-8")


def test_reported_account_failure_uses_single_detailed_warning(tmp_path, monkeypatch):
    account = strategy.Account("300", "300종목", "test", "work", "2026-10-19", None)
    monkeypatch.setattr(strategy, "DATA", tmp_path)
    monkeypatch.setattr(job, "ACCOUNTS", (account,))
    monkeypatch.setattr(job, "load_env", lambda: None)
    monkeypatch.setattr(job, "current_config", lambda _: {"rule": "same"})
    account.path("config.json").write_text(json.dumps({"rule": "same"}), encoding="utf-8")
    ledger = tmp_path / "spy.csv"
    ledger.write_text("date\n20261022\n", encoding="utf-8")
    monkeypatch.setattr(us_spy_hold, "LEDGER_PATH", str(ledger))
    monkeypatch.setattr(us_spy_hold, "update", lambda: 1)
    monkeypatch.setattr(strategy, "update_all", lambda notify: (notify("300종목: 기록된 주문을 재현하지 못해"), {"300": {"state": "diverged"}})[1])
    from stock_alarm import us_account_audit, notifier
    monkeypatch.setattr(us_account_audit, "diagnose", lambda _: {"daily_state_differences": [{"date": "2026-10-21", "fields": {
        "pending_json": {"saved": '["CSX.O"]', "current": '["HON.O"]'}}}]})
    from stock_alarm import us_name_metadata, us_research
    monkeypatch.setattr(us_name_metadata, "enrich_universe_names", lambda: 0)
    monkeypatch.setattr(us_research, "run", lambda: {})
    sent = []
    monkeypatch.setattr(notifier, "send_notification", lambda message, event_type: sent.append((message, event_type)))
    with pytest.raises(job.AccountUpdateError):
        job.run(today=date(2026, 10, 23))
    assert len(sent) == 1 and "CSX.O" in sent[0][0] and "HON.O" in sent[0][0]
    assert sent[0][1] == "us_account"
