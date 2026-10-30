import csv
import json
import os
import random
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_alarm import us_strategy_account as acc
from stock_alarm import us_account_audit as audit

ACCOUNT = acc.Account("t", "테스트 계좌", "t", "work", "2026-03-02", None)


def row(day, event="buy", ticker="AAPL.O", shares=10, price="100.0000", reason="entry"):
    return {"date": day, "event": event, "ticker": ticker, "shares": shares, "price_usd": price, "reason": reason}


def bars(path: Path, days: list[str], seed: int = 1) -> None:
    """Uptrending random walk with a volume spike every 6th day, so the real rules produce orders."""
    rng, price = random.Random(seed), 100.0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for i, day in enumerate(days):
            price *= 1 + 0.003 + rng.uniform(-0.012, 0.012)
            volume = 15_000_000 if i % 6 == 0 else 5_000_000
            w.writerow([day, f"{price * 0.998:.2f}", f"{price * 1.01:.2f}", f"{price * 0.99:.2f}", f"{price:.2f}", volume])


def business_days(n: int, end: date) -> list[str]:
    out, day = [], end
    while len(out) < n:
        if day.weekday() < 5:
            out.append(f"{day:%Y%m%d}")
        day -= timedelta(days=1)
    return out[::-1]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A tiny synthetic market under tmp_path: SPY + 3 stocks, a universe file, all module paths redirected."""
    data = tmp_path / "data"
    (data / "backtest_us" / "ohlcv").mkdir(parents=True)
    days = business_days(330, date(2026, 6, 30))
    for i, code in enumerate(["SPY", "AAA.O", "BBB", "CCC.O"]):
        bars(data / "backtest_us" / "ohlcv" / f"{code}.csv", days, seed=i + 1)
    with open(data / "backtest_us" / "universe.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "name_eng", "market_value_usd"])
        for code in ("AAA.O", "BBB", "CCC.O"):
            w.writerow([code, f"{code} Inc", "100000000000"])
    monkeypatch.setattr(acc, "DATA", data)
    monkeypatch.setattr(acc, "SRC", data / "backtest_us" / "ohlcv")
    monkeypatch.setattr(acc, "BACKUP_DIR", data / "backups" / "us")
    return SimpleNamespace(data=data, days=days, today=date(2026, 6, 30))


def test_decision_rows_convert_cents_to_dollars():
    result = SimpleNamespace(events=[{"date": "2026-10-07", "event": "buy", "ticker": "AAPL.O", "shares": 10, "price": 32513.0, "reason": "entry"}])
    assert acc.decision_rows(result)[0]["price_usd"] == "325.1300"


def test_diverges_accepts_prefix_and_new_orders():
    logged = [row("2026-10-07")]
    assert acc.diverges([], [row("2026-10-07")]) == []
    assert acc.diverges(logged, [row("2026-10-07"), row("2026-10-08", "sell_full", reason="stop")]) == []


def test_diverges_flags_changed_missing_or_extra_orders_on_logged_days():
    logged = [row("2026-10-07")]
    assert acc.diverges(logged, [row("2026-10-07", price="99.0000")])  # price changed
    assert acc.diverges(logged, [])  # order vanished
    assert acc.diverges(logged, [row("2026-10-07"), row("2026-10-07", ticker="MSFT.O")])  # unlogged order on a logged day


def test_update_end_to_end_is_idempotent_and_detects_tampering(world):
    a = ACCOUNT
    first = acc.update(a, failed=[], today=world.today)
    assert first["state"] == "ok", first
    for name in ("equity.csv", "holdings.csv", "trades.csv", "decisions.csv", "status.json", "universe.csv", "config.json"):
        assert a.path(name).exists(), name
    assert (acc.BACKUP_DIR / f"{date.today():%Y%m%d}" / "t_decisions.csv").exists()
    equity = list(csv.DictReader(open(a.path("equity.csv"), encoding="utf-8")))
    assert equity[0]["equity_usd"] == "100000.00" and equity[0]["date"] >= a.start_signal_day
    decisions_before = a.path("decisions.csv").read_text("utf-8")
    assert acc.update(a, failed=[], today=world.today)["state"] == "ok"
    assert a.path("decisions.csv").read_text("utf-8") == decisions_before  # nothing is logged twice
    logged = list(csv.DictReader(open(a.path("decisions.csv"), encoding="utf-8")))
    assert logged, "the synthetic uptrend must produce orders, otherwise this test proves nothing"
    assert list(csv.DictReader(open(a.path("holdings.csv"), encoding="utf-8"))) or list(csv.DictReader(open(a.path("trades.csv"), encoding="utf-8")))
    a.path("decisions.csv").write_text(decisions_before.replace(logged[0]["price_usd"], "1.0000", 1), encoding="utf-8")
    assert acc.update(a, failed=[], today=world.today)["state"] == "diverged"
    assert json.loads(a.path("status.json").read_text("utf-8"))["state"] == "diverged"


def test_update_waits_before_the_start_day(world):
    late = acc.Account("t", "테스트 계좌", "t", "work", "2027-01-04", None)
    assert acc.update(late, failed=[], today=world.today)["state"] == "waiting"
    assert not late.path("equity.csv").exists()


def test_update_halts_when_settings_change(world, monkeypatch):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    monkeypatch.setenv("TOP_N", "3" if os.environ.get("TOP_N") != "3" else "4")
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "config_changed"


def test_validate_halts_on_structure_and_only_reports_big_moves(world):
    ohlcv = acc.SRC
    assert acc.validate(["AAA.O"], {}, world.today)[0] == []
    text = (ohlcv / "AAA.O.csv").read_text("utf-8").splitlines()
    (ohlcv / "BBB.csv").write_text("\n".join(text + [text[-1]]) + "\n", encoding="utf-8")  # duplicated last day
    assert any("duplicate" in p for p in acc.validate(["BBB"], {}, world.today)[0])
    assert any("shrank" in p for p in acc.validate(["AAA.O"], {"AAA.O": 10_000}, world.today)[0])
    assert any("SPY" in p for p in acc.validate(["AAA.O"], {}, world.today + timedelta(days=30))[0])  # stale benchmark
    rows = (ohlcv / "CCC.O.csv").read_text("utf-8").splitlines()
    parts = rows[-1].split(",")
    parts[4] = str(round(float(parts[4]) * 1.6, 2))  # +60% on the newest day: reported, not halted
    (ohlcv / "CCC.O.csv").write_text("\n".join(rows[:-1] + [",".join(parts)]) + "\n", encoding="utf-8")
    problems, _counts, anomalies = acc.validate(["CCC.O"], {}, world.today)
    assert problems == [] and anomalies and anomalies[0]["ticker"] == "CCC.O"


def test_backup_keeps_only_recent_day_folders(world, tmp_path):
    root = tmp_path / "bk"
    for n in range(acc.BACKUP_KEEP_DAYS + 3):
        (root / f"2026{(n // 28) + 1:02d}{(n % 28) + 1:02d}").mkdir(parents=True)
    ACCOUNT.path("decisions.csv").write_text("date\n", encoding="utf-8")
    target = acc.backup(ACCOUNT, root, date(2026, 12, 30))
    assert (target / "t_decisions.csv").exists()
    assert len([p for p in root.iterdir() if p.is_dir()]) == acc.BACKUP_KEEP_DAYS


def test_alert_is_sent_once_per_problem_and_clears_when_ok(world):
    sent = []
    assert acc.alert_if_needed(ACCOUNT, "diverged", sent.append) is True
    assert acc.alert_if_needed(ACCOUNT, "diverged", sent.append) is False  # same problem: no repeat
    assert len(sent) == 1 and "테스트 계좌" in sent[0]
    assert acc.alert_if_needed(ACCOUNT, "ok", sent.append) is False  # recovery clears the memory silently
    assert acc.alert_if_needed(ACCOUNT, "diverged", sent.append) is True  # a new episode alerts again
    assert acc.alert_if_needed(ACCOUNT, "waiting", sent.append) is False


def test_update_all_isolates_a_failing_account_and_alerts(world, monkeypatch):
    import stock_alarm.us_history_collect as collector
    monkeypatch.setattr(collector, "refresh", lambda codes: [])
    monkeypatch.setattr(acc, "ACCOUNTS", (ACCOUNT,))

    def boom(a, failed=None, today=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(acc, "update", boom)
    sent = []
    assert acc.update_all(notify=sent.append) == {"t": {"state": "error"}}
    assert len(sent) == 1 and "오류" in sent[0]
    assert json.loads(ACCOUNT.path("status.json").read_text("utf-8"))["state"] == "error"


def test_no_trade_days_and_order_multiplicity_are_frozen():
    assert acc.diverges([], [row("2026-10-08")], "2026-10-09")
    assert acc.diverges([], [row("2026-10-10")], "2026-10-09") == []
    assert acc.diverges([row("2026-10-07")], [row("2026-10-07"), row("2026-10-07")])
    assert acc.diverges([row("2026-10-07")], [row("2026-10-07", reason="changed")])


def test_finalized_daily_state_cannot_be_rewritten(world, monkeypatch):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    before = {n: ACCOUNT.path(n).read_bytes() for n in ("equity.csv", "decisions.csv", "checkpoint.json")}
    replay = acc.replay
    def changed(a, names):
        engine, sim, result = replay(a, names)
        result.daily_state[0]["equity"] += 100
        return engine, sim, result
    monkeypatch.setattr(acc, "replay", changed)
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "diverged"
    assert before == {n: ACCOUNT.path(n).read_bytes() for n in before}
    assert acc.read_status(ACCOUNT)["last_day"] == "2026-06-30"


def test_legacy_bootstrap_checks_equity_before_checkpoint(world):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    ACCOUNT.path("checkpoint.json").unlink()  # only the fixture's temporary account
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    ACCOUNT.path("checkpoint.json").unlink()
    equity = ACCOUNT.path("equity.csv")
    equity.write_text(equity.read_text("utf-8").replace("100000.00", "99999.00", 1), encoding="utf-8")
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "diverged"
    assert not ACCOUNT.path("checkpoint.json").exists()


def test_partial_ledger_write_is_detected(world):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    with ACCOUNT.path("holdings.csv").open("a", encoding="utf-8") as f:
        f.write("broken")
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "diverged"


def test_new_observed_day_extends_checkpoint(world):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    for p in acc.SRC.glob("*.csv"):
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
        new = dict(rows[-1], date="20260701")
        acc._write_csv(p, list(new), [new], mode="a")
    assert acc.update(ACCOUNT, failed=[], today=date(2026, 7, 1))["state"] == "ok"
    assert json.loads(ACCOUNT.path("checkpoint.json").read_text("utf-8"))["finalized_through"] == "2026-07-01"


def test_interrupted_first_commit_cannot_be_bootstrapped(world, monkeypatch):
    original = acc.write_ledgers
    def interrupt(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(acc, "write_ledgers", interrupt)
    with pytest.raises(OSError):
        acc.update(ACCOUNT, failed=[], today=world.today)
    monkeypatch.setattr(acc, "write_ledgers", original)
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "data_problem"
    assert not ACCOUNT.path("checkpoint.json").exists()


def test_failed_download_does_not_finalize_old_data(world):
    assert acc.update(ACCOUNT, failed=[("AAA.O", "timeout")], today=world.today)["state"] == "data_problem"
    assert not ACCOUNT.path("equity.csv").exists()


@pytest.mark.parametrize("setting", ["MAX_ENTRY_DAY_CHANGE_PCT", "TAKE_PROFIT_1_PCT"])
def test_additional_strategy_settings_are_frozen(world, monkeypatch, setting):
    acc.freeze(ACCOUNT)
    monkeypatch.setenv(setting, "999")
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "config_changed"


def test_engine_version_change_preserves_ledgers(world, monkeypatch):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    before = ACCOUNT.path("equity.csv").read_bytes()
    original = acc.current_config
    def changed(a):
        config = original(a)
        config["engine_hashes"]["app.py"] = "new version"
        return config
    monkeypatch.setattr(acc, "current_config", changed)
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "config_changed"
    assert ACCOUNT.path("equity.csv").read_bytes() == before


def test_input_snapshot_is_immutable_and_identifies_changed_bars(world):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    saved = Path(audit.snapshot(ACCOUNT))
    original = saved.read_bytes()
    path = acc.SRC / "AAA.O.csv"
    rows = path.read_text(encoding="utf-8").splitlines()
    parts = rows[-1].split(",")
    parts[4] = str(float(parts[4]) + 1)
    path.write_text("\n".join([*rows[:-1], ",".join(parts)]) + "\n", encoding="utf-8")
    assert Path(audit.snapshot(ACCOUNT)).read_bytes() == original
    changes = audit._changed_bars(ACCOUNT, "2026-06-30")
    assert changes["snapshot_available"] is True
    assert any(row["ticker"] == "AAA.O" and row["date"] == "20260630" for row in changes["changes"])


def test_diagnosis_reports_changed_finalized_state_without_rewriting_ledger(world, monkeypatch):
    assert acc.update(ACCOUNT, failed=[], today=world.today)["state"] == "ok"
    audit.snapshot(ACCOUNT)
    before = ACCOUNT.path("checkpoint.json").read_bytes()
    original = acc.replay

    def changed(a, names):
        engine, sim, result = original(a, names)
        result.daily_state[-1]["pending_json"] = '["OTHER"]'
        return engine, sim, result

    monkeypatch.setattr(acc, "replay", changed)
    report = audit.diagnose(ACCOUNT)
    assert report["daily_state_differences"][-1]["fields"]["pending_json"]["current"] == '["OTHER"]'
    assert report["ledger_hashes_match"] is True
    assert Path(report["current_input_archive"]).exists()
    assert ACCOUNT.path("checkpoint.json").read_bytes() == before


def test_legacy_engine_binding_requires_exact_prefix_replay(world):
    assert acc.update(ACCOUNT,failed=[],today=world.today)['state']=='ok'
    before=acc.ledger_hashes(ACCOUNT)
    old=json.loads(ACCOUNT.path('config.json').read_text())
    old.pop('engine_bundle_sha256');old.pop('replay_contract')
    ACCOUNT.path('config.json').write_text(json.dumps(old))
    assert acc.migrate_legacy_engine(ACCOUNT)['state']=='bound'
    assert acc.ledger_hashes(ACCOUNT)==before
    assert json.loads(ACCOUNT.path('config.json').read_text())==acc.current_config(ACCOUNT)


def test_provider_revisions_cannot_change_confirmed_pending_orders(world):
    assert acc.update(ACCOUNT,failed=[],today=world.today)['state']=='ok'
    before=ACCOUNT.path('checkpoint.json').read_bytes()
    source=acc.SRC/'AAA.O.csv'
    rows=list(csv.DictReader(source.open()))
    rows[-1]['volume']=str(int(rows[-1]['volume'])*10)
    with source.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    assert acc.update(ACCOUNT,failed=[],today=world.today)['state']=='ok'
    assert ACCOUNT.path('checkpoint.json').read_bytes()==before


def test_missing_latest_constituent_blocks_publication(world):
    assert acc.update(ACCOUNT,failed=[],today=world.today)['state']=='ok'
    before=acc.ledger_hashes(ACCOUNT)
    source=acc.SRC/'CCC.O.csv'
    rows=source.read_text().splitlines();source.write_text('\n'.join(rows[:-1])+'\n')
    result=acc.update(ACCOUNT,failed=[],today=world.today)
    assert result['state']=='data_problem'
    assert any('CCC.O: missing benchmark sessions' in s for s in result['problems'])
    assert acc.ledger_hashes(ACCOUNT)==before


def test_engine_bundle_is_distinct_and_tampering_is_rejected(tmp_path,monkeypatch):
    from stock_alarm import us_engine,app
    assert us_engine.module('app') is not app
    invalid=tmp_path/'invalid.zip';invalid.write_bytes(b'not the engine')
    monkeypatch.setattr(us_engine,'BUNDLE',invalid)
    with pytest.raises(ValueError,match='bundle changed'):us_engine.verify()


def test_missing_confirmed_inputs_refuses_legacy_binding(world):
    assert acc.update(ACCOUNT,failed=[],today=world.today)['state']=='ok'
    config=json.loads(ACCOUNT.path('config.json').read_text())
    config.pop('engine_bundle_sha256');config.pop('replay_contract')
    ACCOUNT.path('config.json').write_text(json.dumps(config))
    archive=acc.BACKUP_DIR/'inputs'/ACCOUNT.key/'2026-06-30.zip';archive.unlink()
    before=acc.ledger_hashes(ACCOUNT)
    with pytest.raises(ValueError,match='archive missing'):acc.migrate_legacy_engine(ACCOUNT)
    assert acc.ledger_hashes(ACCOUNT)==before
    assert json.loads(ACCOUNT.path('config.json').read_text())==config
