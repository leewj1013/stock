import sqlite3

import pytest

from stock_alarm import profit_research as research
from test_profit_research import TRADE, check, sale


def simulate(checks, sales=()):
    return research.simulate(TRADE, checks, sales, dips=True)


def test_zero_requires_prior_positive_observation():
    result = simulate([check(1, 100), check(2, 99), check(3, 97), check(4, 96)])
    assert not result["had_profit"]
    assert not result["arms"]["zero"].get("signal_at")
    assert result["arms"]["minus3"]["exits"][0]["price"] == 96
    assert result["arms"]["minus5"]["remaining"] == 10


def test_zero_return_after_profit_fills_next_price_with_gap():
    result = simulate([check(1, 102), check(2, 100), check(3, 98)])
    zero = result["arms"]["zero"]
    assert zero["signal_at"] == check(2, 100)["checked_at"]
    assert zero["exits"][0]["price"] == 98
    assert zero["net_return_pct"] < -2


def test_three_thresholds_are_independent_not_cascading():
    result = simulate([check(1, 102), check(2, 100), check(3, 97), check(4, 96),
                       check(5, 95), check(6, 94)], [sale(7, 90, 10)])
    assert [result["arms"][k]["exits"][0]["price"] for k in research.DIP_LABELS] == [97, 96, 94]
    assert result["complete"]
    assert all(result["arms"][k]["remaining"] == 0 for k in research.DIP_LABELS)


def test_partial_profit_is_preserved_and_only_remainder_stops():
    result = simulate([check(2, 96), check(3, 94), check(4, 93)],
                      [sale(1, 110, 5, "take_profit_1"), sale(5, 90, 5)])
    arm = result["arms"]["minus5"]
    assert [(r["price"], r["quantity"]) for r in arm["exits"]] == [(110, 5), (93, 5)]
    assert arm["net_return_pct"] > 0


def test_recovery_only_counts_after_experimental_fill_and_before_baseline_exit():
    result = simulate([check(1, 97), check(2, 95), check(3, 110), check(4, 121), check(6, 150)],
                      [sale(5, 120, 10)])
    assert result["arms"]["minus3"]["post_exit_max_return_pct"] == pytest.approx(21)
    assert result["arms"]["minus5"]["exits"][0]["price"] == 110
    assert result["arms"]["minus5"]["post_exit_max_return_pct"] == pytest.approx(21)


def test_no_next_price_leaves_signal_pending_and_case_open():
    result = simulate([check(1, 95)])
    assert not result["complete"]
    assert result["arms"]["minus5"]["pending"] == 10
    assert not result["arms"]["minus5"].get("extra_exit_at")


def test_separate_forward_registration_collection_and_recovery(tmp_path):
    source, path, old_path = [str(tmp_path / name) for name in ("source.db", "dips.db", "profit.db")]
    with research.connect(old_path) as db:
        old_start = db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
    with research.connect(path, version=research.DIP_VERSION, rules=research.DIP_RULES) as db:
        db.execute("UPDATE metadata SET value='2026-10-29T09:00:00' WHERE key='activated_at'")
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE virtual_trades(trade_id INTEGER,created_at TEXT,ticker TEXT,price INTEGER,quantity INTEGER);
            CREATE TABLE virtual_sales(sale_id INTEGER,created_at TEXT,ticker TEXT,price INTEGER,quantity INTEGER,stage TEXT,reason TEXT);
            CREATE TABLE position_checks(checked_at TEXT,ticker TEXT,close INTEGER,return_pct REAL);
            INSERT INTO virtual_trades VALUES(1,'2026-10-28T09:00:00','A',100,10);
            INSERT INTO virtual_trades VALUES(2,'2026-10-29T09:00:00','A',100,10);
            INSERT INTO virtual_sales VALUES(1,'2026-10-29T09:10:00','A',90,10,'','기존 매도');
        """)
        db.executemany("INSERT INTO position_checks VALUES(?,?,?,?)",
                       [(check(i, p)["checked_at"], "A", p, p-100)
                        for i, p in enumerate((102, 100, 97, 96, 95, 94, 110, 121), 1)])
    assert research.run_dips(source, path) == {"version": research.DIP_VERSION, "added": 1, "updated": 1, "failures": 0}
    assert research.run_dips(source, path)["updated"] == 0
    data = research.dip_dashboard_data(path)
    assert data["total"] == data["complete"] == 1 and data["errors"] == 0
    assert all(row[1:4] == ["1", "1", "1"] for row in data["results"])
    assert all(row[3:] == ["1", "1/1 · 100.0%", "1/1 · 100.0%"] for row in data["recovery"])
    with research.connect(old_path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0] == old_start
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 0
    assert "0%·-3%·-5%" in research.dashboard_section(old_path)
    with pytest.raises(ValueError, match="rules changed"):
        research.connect(path)  # An old research reader cannot repurpose the dip database.


def test_no_data_view_does_not_create_dip_database(tmp_path):
    path = str(tmp_path / "absent.db")
    data = research.dip_dashboard_data(path)
    assert data["total"] == data["complete"] == 0
    assert all(row[4] == "관찰 중" for row in data["results"])
    assert not (tmp_path / "absent.db").exists()
