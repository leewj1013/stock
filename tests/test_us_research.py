import csv
import json
from contextlib import closing

from stock_alarm import us_research as research
from stock_alarm import us_strategy_account as strategy


def bars(values):
    return [{"date": day, "open": str(open_), "high": str(max(open_, close)),
             "low": str(min(open_, close)), "close": str(close), "volume": "1000"}
            for day, open_, close in values]


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_gap_and_risk_variants_are_fixed_before_outcomes():
    stock = bars([("20261019", 100, 100), ("20261020", 104, 105), ("20261021", 106, 110), ("20261022", 111, 112)])
    spy = bars([(f"202609{i:02d}", 100, 100) for i in range(1, 20)] + [("20261020", 99, 90)])
    ctx = research.context("X", "2026-10-20", stock, spy, vix=30)
    result = research.simulate(ctx, earnings=True)

    assert round(ctx["gap_pct"], 1) == 4.0 and ctx["defensive"] is True
    assert result["1"]["gap_filter"] == 0.0
    assert result["1"]["risk_scaled"] == round(result["1"]["immediate"] * 0.5, 4)
    assert result["3"]["confirmed"] != 0.0 and result["3"]["earnings_confirmed"] == result["3"]["confirmed"]


def test_harvest_keeps_frozen_ledgers_read_only_and_deduplicates(tmp_path, monkeypatch):
    account = strategy.Account("100", "test", "research_account", "work", "2026-10-19", None)
    monkeypatch.setattr(strategy, "DATA", tmp_path)
    monkeypatch.setattr(research, "ACCOUNTS", (account,))
    source = tmp_path / "ohlcv"
    monkeypatch.setattr(research, "SRC", source)
    write_csv(source / "X.csv", bars([("20261019", 100, 100), ("20261020", 101, 102)]))
    write_csv(source / "SPY.csv", bars([("20261019", 100, 100), ("20261020", 100, 101)]))
    write_csv(account.path("universe.csv"), [{"code": "X", "name_eng": "Example", "name_ko": "예시"}])
    decisions = [{"date": "2026-10-20", "event": "buy", "ticker": "X", "shares": "5", "price_usd": "101", "reason": "signal"}]
    write_csv(account.path("decisions.csv"), decisions)
    original = account.path("decisions.csv").read_bytes()
    db_path = tmp_path / "research.db"

    event = lambda *_: {"form": "10-Q", "accepted_at": "2026-10-19T20:00:00", "accession": "a"}
    assert research.harvest(db_path, event) == 1
    assert research.harvest(db_path, event) == 0
    assert account.path("decisions.csv").read_bytes() == original
    with closing(research.connect(db_path)) as db:
        row = db.execute("SELECT * FROM episodes").fetchone()
        assert row["name"] == "예시" and row["sec_form"] == "10-Q"


def test_dashboard_explains_empty_forward_observation(tmp_path):
    path = tmp_path / "us.db"
    with closing(research.connect(path)):
        pass
    page = research.dashboard_section(path)
    assert "미국 진입 관찰실험" in page and "첫 가상매수 체결을 기다리고" in page


def test_risk_choice_is_invariant_to_entry_session_closes(monkeypatch):
    stock=bars([('20261019',100,100),('20261020',100,110)])
    prior=[(f'202609{i:02d}',100,100) for i in range(1,21)]
    spy_a=bars(prior+[('20261020',100,50)])
    spy_b=bars(prior+[('20261020',100,200)])
    looked=[];monkeypatch.setattr(research,'_vix',lambda day:looked.append(day) or 20)
    a=research.context('X','2026-10-20',stock,spy_a)
    b=research.context('X','2026-10-20',stock,spy_b)
    assert a['defensive']==b['defensive']==False
    assert a['spy_ma20']==b['spy_ma20']==100
    assert looked==['2026-09-20','2026-09-20']
    assert research.simulate(a,False)['1']['risk_scaled']==research.simulate(b,False)['1']['risk_scaled']
