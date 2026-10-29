import sqlite3

import pytest

from stock_alarm import profit_research as research


TRADE = {"created_at": "2026-10-29T09:00:00", "price": 100, "quantity": 10}


def test_historical_report_missing_or_invalid_is_unavailable(tmp_path):
    path = tmp_path / 'report.json'
    assert research.historical_dashboard_data(path) == {'available': False}
    path.write_text('{"performance":[]}', encoding='utf-8')
    assert research.historical_dashboard_data(path) == {'available': False}


def test_historical_report_formats_saved_results(tmp_path):
    import json
    path = tmp_path / 'report.json'
    path.write_text(json.dumps({'generated_at':'2026-10-29','included':2,'excluded':{'open':1},
        'performance':[{'label':'<실험>','n':2,'mean_pct':1,'median_pct':0,'win_rate_pct':50,
                        'worst_pct':-2,'mean_difference_pctp':.5,'improved':1,'neutral':1,'worsened':0}],
        'threshold_outcomes':[{'threshold_pct':-5,'hit':1,'of_all_pct':50,'later_above_entry':0,
                              'profitable_exit':0,'profitable_of_hit_pct':0,'loss_exit':1,'loss_of_hit_pct':100}],
        'final_sale_handling':[{'tp1':True,'stage':'final','reason':'손절','count':1}]}),encoding='utf-8')
    data = research.historical_dashboard_data(path)
    assert data['available'] and data['excluded'] == 1
    assert data['performance'][0][6:] == ['+0.500%p','1/1/0']
    assert data['thresholds'][0][-1] == '1 (100.0%)'
    assert data['handling'][0][0] == '체결'


def check(minute, price):
    return {"checked_at": f"2026-10-29T09:{minute:02d}:00", "close": price}


def sale(minute, price, quantity, stage=""):
    return {"created_at": f"2026-10-29T09:{minute:02d}:00", "price": price,
            "quantity": quantity, "stage": stage, "reason": "기존 규칙"}


def test_early_signal_fills_next_observation_and_includes_both_legs():
    result = research.simulate(TRADE, [check(1, 105), check(2, 103)], [sale(4, 95, 10)])
    early = result["arms"]["early"]
    assert [(r["price"], r["quantity"]) for r in early["exits"]] == [(103, 5), (95, 5)]
    assert result["complete"]
    assert early["net_return_pct"] > result["arms"]["baseline"]["net_return_pct"]
    assert early["net_return_pct"] < -1  # Both entry and exit costs apply.


def test_recovery_below_ten_does_not_trigger_principal_protection():
    result = research.simulate(TRADE, [check(2, 108), check(3, 120)],
                               [sale(1, 110, 5, "take_profit_1"), sale(4, 121, 5, "take_profit_2")])
    assert result["arms"]["principal"]["net_return_pct"] == result["arms"]["baseline"]["net_return_pct"]


def test_principal_and_trail_fill_after_signal_even_with_downward_gap():
    result = research.simulate(TRADE, [check(2, 115), check(3, 110), check(4, 99), check(5, 95)],
                               [sale(1, 110, 5, "take_profit_1"), sale(6, 90, 5)])
    assert result["arms"]["trail"]["exits"][-1]["price"] == 99
    assert result["arms"]["principal"]["exits"][-1]["price"] == 95


def test_pending_open_case_is_not_completed_and_single_share_has_no_half_sale():
    result = research.simulate({**TRADE, "quantity": 1}, [check(1, 108)], [])
    assert not result["complete"]
    assert result["arms"]["early"]["pending"] == 0
    assert result["arms"]["early"]["remaining"] == 1


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_invalid_prices_rejected(price):
    with pytest.raises(ValueError):
        research.simulate(TRADE, [check(1, price)], [])


def test_overselling_rejected():
    with pytest.raises(ValueError, match="exceed"):
        research.simulate(TRADE, [], [sale(1, 110, 11)])


def test_forward_only_idempotent_collection_and_completed_results(tmp_path):
    source, path = str(tmp_path / "source.db"), str(tmp_path / "research.db")
    with research.connect(path) as db:
        db.execute("UPDATE metadata SET value='2026-10-29T09:00:00' WHERE key='activated_at'")
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE virtual_trades(trade_id INTEGER,created_at TEXT,ticker TEXT,price INTEGER,quantity INTEGER);
            CREATE TABLE virtual_sales(sale_id INTEGER,created_at TEXT,ticker TEXT,price INTEGER,quantity INTEGER,stage TEXT,reason TEXT);
            CREATE TABLE position_checks(checked_at TEXT,ticker TEXT,close INTEGER,return_pct REAL);
            INSERT INTO virtual_trades VALUES(1,'2026-10-28T09:00:00','A',100,10);
            INSERT INTO virtual_trades VALUES(2,'2026-10-29T09:00:00','A',100,10);
            INSERT INTO position_checks VALUES('2026-10-29T09:01:00','A',105,5);
            INSERT INTO position_checks VALUES('2026-10-29T09:02:00','A',106,6);
            INSERT INTO virtual_sales VALUES(1,'2026-10-29T09:04:00','A',95,10,'','기간청산');
        """)
    assert research.run(source, path) == {"version": research.VERSION, "added": 1, "updated": 1, "failures": 0}
    assert research.run(source, path)["added"] == 0
    data = research.dashboard_data(path)
    assert data["total"] == data["complete"] == 1
    assert data["results"][0][1] == "1"
    assert data["results"][1][1] == "0"  # No first take-profit fill.
    assert data["plans"][0][0].startswith("하락장")
    assert "거래별 과거 비교 · 계좌 실험 미실행" in research.dashboard_section(path)


def test_read_only_missing_data_and_version_guard(tmp_path):
    path = str(tmp_path / "research.db")
    assert research.dashboard_data(path)["total"] == 0
    assert not (tmp_path / "research.db").exists()
    with research.connect(path) as db:
        db.execute("UPDATE metadata SET value='{}' WHERE key='rules'")
    with pytest.raises(ValueError, match="rules changed"):
        research.connect(path)


def test_open_case_is_excluded_from_performance_average(tmp_path):
    path = str(tmp_path / "research.db")
    result = research.simulate(TRADE, [check(1, 110)], [])
    import json
    with research.connect(path) as db:
        db.execute("INSERT INTO cases(trade_id,ticker,bought_at,payload,outcomes) VALUES(1,'A',?,'{}',?)",
                   (TRADE["created_at"], json.dumps(result)))
    data = research.dashboard_data(path)
    assert data["total"] == 1 and data["complete"] == 0
    assert all(row[1] == "0" and row[2] == "관찰 중" for row in data["results"])
