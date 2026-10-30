import hashlib
import json
import sqlite3

import pytest

from stock_alarm.decision_evidence import append, schema
from stock_alarm import data_store as store
from stock_alarm.decision_evidence import backfill, dashboard_data
from unittest.mock import patch


def test_evidence_is_first_write_wins_and_cannot_be_updated_or_deleted():
    with sqlite3.connect(':memory:') as db:
        schema(db)
        append(db, 'buy', 1, 'A', '2026-10-30T10:00:00', {'price': 100})
        append(db, 'buy', 1, 'A', '2026-10-30T10:00:00', {'price': 200})
        text, digest = db.execute('SELECT payload,digest FROM decision_evidence').fetchone()
        assert json.loads(text)['price'] == 100
        assert hashlib.sha256(text.encode()).hexdigest() == digest
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE decision_evidence SET payload='{}'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM decision_evidence')


def test_rolled_back_trade_cannot_leave_orphan_evidence():
    with sqlite3.connect(':memory:') as db:
        schema(db)
        db.execute('BEGIN')
        append(db, 'buy', 1, 'A', '2026-10-30T10:00:00', {'price': 100})
        db.rollback()
        assert db.execute('SELECT COUNT(*) FROM decision_evidence').fetchone()[0] == 0


def test_buy_and_partial_sell_archive_the_actual_input_and_quantity(tmp_path, monkeypatch):
    path = str(tmp_path / 'account.db')
    monkeypatch.setenv('TAKE_PROFIT_1_PCT', '10')
    store.virtual_deposit(10000, path)
    store.virtual_buy([{'ticker': 'A', 'name': '<종목>', 'close': 100, 'score': 0, 'allocation_pct': 20}], path)
    store.virtual_sell([{'ticker': 'A', 'close': 110, 'sale_type': 'partial', 'quantity_fraction': .5,
                        'reason': '1차 익절', 'decision_context': {'sell_policy': {'take_profit_1_pct': 10}}}], path)
    rows = store.query_rows('SELECT * FROM decision_evidence ORDER BY evidence_id', path=path)
    assert [r['kind'] for r in rows] == ['buy', 'sell']
    buy, sale = [json.loads(r['payload']) for r in rows]
    assert buy['execution']['quantity'] == 20
    assert sale['execution']['quantity'] == 10
    assert sale['input']['decision_context']['sell_policy']['take_profit_1_pct'] == 10
    monkeypatch.setenv('TAKE_PROFIT_1_PCT', '99')
    assert json.loads(store.query_rows("SELECT payload FROM decision_evidence WHERE kind='buy'", path=path)[0]['payload'])['rules']['environment']['TAKE_PROFIT_1_PCT'] == '10'
    assert any(row[5] == '0' for row in dashboard_data(path)['rows'])


def test_trade_is_rolled_back_if_its_evidence_cannot_be_saved(tmp_path):
    path = str(tmp_path / 'account.db')
    store.virtual_deposit(10000, path)
    with patch('stock_alarm.decision_evidence.append', side_effect=sqlite3.OperationalError('disk failure')):
        with pytest.raises(sqlite3.OperationalError):
            store.virtual_buy([{'ticker': 'A', 'close': 100, 'allocation_pct': 20}], path)
    assert not store.query_rows('SELECT * FROM virtual_trades', path=path)
    assert store.query_rows('SELECT cash FROM virtual_accounts', path=path)[0]['cash'] == 10000


def test_recommendations_keep_separate_profile_scores_and_original_values(tmp_path):
    path = str(tmp_path / 'account.db')
    with patch('stock_alarm.data_store.strategy_identity', return_value={'git_commit':'test','config_hash':'fixed','watchlist_hash':'fixed'}):
        run = store.start_run('recommendation', path=path)
    for profile, score in (('aggressive', 70), ('neutral', 60)):
        store.write_profile_selections(run, profile, [{'ticker':'A','selected':1,'profile_score':score}], path)
    candidate = {'ticker':'A','evaluated_at':'2026-10-30T10:00:00','close':100,'passed':1,'selected':1,'final_score':70}
    store.write_candidates(run, [candidate], path)
    store.write_candidates(run, [{**candidate,'close':200}], path)
    evidence = store.query_rows("SELECT * FROM decision_evidence WHERE kind='recommendation'", path=path)
    assert len(evidence) == 2
    assert {json.loads(r['payload'])['candidate']['profile_score'] for r in evidence} == {60,70}
    assert all(json.loads(r['payload'])['candidate']['close']==100 for r in evidence)
    store.finish_run(run, 'failed', path)
    assert all('실패' in r[9] for r in dashboard_data(path)['rows'])


def test_backfill_never_claims_old_trades_were_captured_live(tmp_path):
    path = str(tmp_path / 'account.db')
    store.virtual_deposit(10000, path)
    with store.connect(path) as db:
        db.execute("INSERT INTO virtual_trades(created_at,ticker,price,quantity,cost,allocation_pct) VALUES('2026-10-29T10:00:00','A',100,1,100,1)")
    assert backfill(path) == 1
    assert backfill(path) == 0
    row = store.query_rows('SELECT * FROM decision_evidence', path=path)[0]
    assert row['origin'] == 'restored'
    assert 'rules' not in json.loads(row['payload'])
