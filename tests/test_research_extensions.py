import sqlite3

import pytest

from stock_alarm import research_extensions as research


@pytest.fixture(autouse=True)
def isolate_evidence(monkeypatch):
    monkeypatch.setattr("stock_alarm.research_data_collect.dashboard_data",lambda:{
        "quotes":{"n":0},"announcements":{"n":0},"statements":{"n":0,"tickers":0},"statuses":[]})


def buy(id=1, day="2026-10-26", quantity=10):
    return {"trade_id": id, "ticker": "A", "name": "종목", "created_at": day+"T09:00:00", "price": 100, "quantity": quantity}


def sale(day, price, quantity):
    return {"ticker": "A", "created_at": day+"T10:00:00", "price": price, "quantity": quantity}


def test_both_sale_legs_and_costs_are_included_but_open_positions_are_excluded():
    rows = research.trade_outcomes([buy(), buy(2,"2026-10-28")],
                                   [sale("2026-10-26",110,5),sale("2026-10-27",90,5)])
    assert rows[0]["state"] == "OPEN" and rows[0]["net_profit"] is None
    assert rows[1]["net_profit"] == pytest.approx(-3)
    assert research.concentration(rows)["complete"] == 1


def test_concentration_removes_only_positive_profit_not_best_loss():
    data = research.concentration([{"state":"COMPLETE","net_profit":v} for v in [100,50,-80]])
    assert data["rows"][0][3] == "+70원"
    assert data["rows"][1][3] == "-30원"
    assert data["rows"][2][2] == "2"
    assert data["rows"][2][3] == "-80원"
    assert research.concentration([{"state":"COMPLETE","net_profit":-10}])["rows"][1][2] == "0"


def test_overlapping_or_oversold_trade_is_not_completed():
    assert research.trade_outcomes([buy(), buy(2,"2026-10-27")], [])[1]["state"] == "ERROR"
    assert research.trade_outcomes([buy()], [sale("2026-10-27",100,11)])[0]["state"] == "ERROR"


def test_expiry_uses_next_observed_trading_session_over_weekend():
    days = ["2026-10-23","2026-10-26","2026-10-27"]
    assert research.expiry_at("2026-10-23T10:00:00","2026-10-27T09:00:00",days) == "2026-10-26T15:30:00"
    assert research.expiry_at("2026-10-23T10:00:00","2026-10-28T09:00:00",days) is None
    assert research.expiry_at(None,"2026-10-26T09:00:00",days) is None


def test_forward_expiry_uses_only_prior_recommendations_and_freezes_signal(tmp_path):
    source, path = str(tmp_path/"source.db"), str(tmp_path/"research.db")
    with research.connect(path) as db:
        db.execute("UPDATE metadata SET value='2026-10-26T00:00:00' WHERE key='activated_at'")
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE virtual_trades(trade_id INTEGER,created_at TEXT,ticker TEXT,price INTEGER,quantity INTEGER);
            CREATE TABLE virtual_sales(sale_id INTEGER,created_at TEXT,ticker TEXT,price INTEGER,quantity INTEGER);
            CREATE TABLE candidate_snapshots(ticker TEXT,selected INTEGER,evaluated_at TEXT);
            INSERT INTO virtual_trades VALUES(1,'2026-10-23T09:00:00','A',100,10);
            INSERT INTO virtual_trades VALUES(2,'2026-10-27T09:00:00','A',100,10);
            INSERT INTO virtual_trades VALUES(3,'2026-10-27T09:00:00','B',100,10);
            INSERT INTO virtual_trades VALUES(4,'2026-10-27T09:00:00','C',100,10);
            INSERT INTO virtual_sales VALUES(1,'2026-10-26T10:00:00','A',110,10);
            INSERT INTO virtual_sales VALUES(2,'2026-10-28T10:00:00','A',90,10);
            INSERT INTO virtual_sales VALUES(3,'2026-10-28T10:00:00','B',110,10);
            INSERT INTO candidate_snapshots VALUES('A',1,'2026-10-23T08:50:00');
            INSERT INTO candidate_snapshots VALUES('A',1,'2026-10-28T08:50:00');
            INSERT INTO candidate_snapshots VALUES('B',1,'2026-10-27T08:50:00');
        """)
    days=["2026-10-23","2026-10-26","2026-10-27","2026-10-28"]
    result=research.run(source,path,sessions=days)
    assert result["failures"] == 0 and result["expiry_cases"] == 3
    with research.connect(path) as db:
        expired=db.execute("SELECT * FROM expiry_cases WHERE trade_id=2").fetchone()
        assert expired["signal_at"] == "2026-10-23T08:50:00"
        assert expired["candidate_profit"] == 0
        assert expired["baseline_profit"] == pytest.approx(-102.85)
    data=research.dashboard_data(path)
    assert data["diagnostic"]["complete"] == 3  # Historical diagnosis is labelled separately.
    assert data["expiry_rows"][0][1:4] == ["3","2","1"]
    assert data["expiry_rows"][0][4:6] == ["+103원","1"]
    assert len(data["plans"]) == 4
    assert data["plans"][2][1] == "수집 연결 · 호가 표본 대기"
    assert data["plans"][3][1] == "수집 연결 · 예고 공시 대기"
    assert research.run(source,path,sessions=days)["expiry_cases"] == 3
    assert "사후" in research.dashboard_section(path)


def test_missing_view_is_read_only_and_rules_are_frozen(tmp_path):
    path=str(tmp_path/"research.db")
    assert research.dashboard_data(path)["diagnostic_at"] == "미실행"
    assert not (tmp_path/"research.db").exists()
    with research.connect(path) as db:
        db.execute("UPDATE metadata SET value='{}' WHERE key='rules'")
    with pytest.raises(ValueError,match="rules changed"):
        research.connect(path)
