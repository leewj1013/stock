import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from unittest.mock import Mock

import pytest

from stock_alarm import data_store
from stock_alarm.account_valuation import value_saved_account
from stock_alarm.premarket_valuation import KST, read_premarket, refresh, refresh_label, validate_mark


def account(tmp_path):
    path = str(tmp_path / 'account.db')
    data_store.virtual_deposit(1000, path)
    with closing(data_store.connect(path)) as db, db:
        db.execute('UPDATE virtual_accounts SET cash=400')
        for ticker in ('A', 'B'):
            db.execute('''INSERT INTO virtual_trades(created_at,ticker,name,price,quantity,cost,allocation_pct,score)
                          VALUES('2026-10-28T10:00:00',?,?,100,3,300,30,0)''', (ticker, ticker))
    return path, {'test': {'db_path': path}}, str(tmp_path / 'display.db')


def rows():
    return [dict(symbol=t, lastPrice=price, currency='KRW', timestamp='2026-10-29T19:59:00+09:00')
            for t, price in [('A', '110'), ('B', '90')]]


def ledger_hash(path):
    with closing(sqlite3.connect(path)) as db:
        data = {t: db.execute('SELECT * FROM ' + t).fetchall() for t in
                ('virtual_accounts', 'virtual_trades', 'virtual_sales', 'virtual_deposits',
                 'virtual_valuation_snapshots', 'portfolio_risk_snapshots', 'price_quality')}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def test_refresh_updates_display_prices_only_and_deduplicates_profiles(tmp_path):
    path, profiles, store = account(tmp_path)
    other_path, _, _ = account(tmp_path / 'other')
    profiles['other'] = {'db_path': other_path}
    before = ledger_hash(path)
    other_before = ledger_hash(other_path)
    client = Mock(); client.prices.return_value = rows()
    result = refresh(datetime(2026, 10, 30, 8, 30, tzinfo=KST), profiles, client, store)
    client.prices.assert_called_once_with(['A', 'B'])
    assert result['succeeded'] == 2
    assert result['accounts'] == 2
    marks, status = read_premarket(path, store)
    state = value_saved_account(data_store.virtual_trader_state(path=path), [], [], marks)
    assert state['total_equity'] == 1000
    assert state['holdings'][0]['price_data_at'] == '2026-10-29T19:59:00+09:00'
    assert status['state'] == 'completed'
    assert '거래시장 미제공' in refresh_label(status)
    assert ledger_hash(path) == before
    assert ledger_hash(other_path) == other_before
    later = value_saved_account(data_store.virtual_trader_state(path=path),
        [dict(ticker=t, checked_at='2026-10-30T09:05:00', close=120) for t in ('A', 'B')], [], marks)
    assert later['holdings'][0]['current_price'] == 120
    assert later['holdings'][0]['price_data_at'] == ''


def test_failed_refresh_retains_previous_valid_mark_and_displays_failure(tmp_path):
    path, profiles, store = account(tmp_path)
    client = Mock(); client.prices.return_value = rows()
    refresh(datetime(2026, 10, 30, 8, 30, tzinfo=KST), profiles, client, store)
    client.prices.side_effect = OSError('offline')
    result = refresh(datetime(2026, 10, 30, 8, 35, tzinfo=KST), profiles, client, store)
    marks, status = read_premarket(path, store)
    assert result['failed_tickers'] == ['A', 'B']
    assert status['state'] == 'failed'
    assert len(marks) == 2
    assert '마지막 유효가격 유지' in refresh_label(status)
    assert value_saved_account(data_store.virtual_trader_state(path=path), [], [], marks)['total_equity'] == 1000


def test_partial_refresh_only_updates_valid_tickers(tmp_path):
    path, profiles, store = account(tmp_path)
    client = Mock(); client.prices.return_value = rows()
    refresh(datetime(2026, 10, 30, 8, 30, tzinfo=KST), profiles, client, store)
    client.prices.return_value = [dict(symbol='A', lastPrice='120', currency='KRW', timestamp='2026-10-30T08:34:00+09:00')]
    refresh(datetime(2026, 10, 30, 8, 35, tzinfo=KST), profiles, client, store)
    marks, status = read_premarket(path, store)
    assert status['state'] == 'partial'
    assert status['failed_tickers'] == ['B']
    assert {m['ticker']: m['price'] for m in marks} == {'A': 120, 'B': 90}


@pytest.mark.parametrize('day,hour,minute', [(30, 7, 59), (30, 8, 50), (30, 22, 0), (31, 8, 30)])
def test_outside_window_does_not_query_or_create_files(tmp_path, day, hour, minute):
    client = Mock()
    store = str(tmp_path / 'unused.db')
    assert refresh(datetime(2026, 10, day, hour, minute, tzinfo=KST), {}, client, store)['state'] == 'skipped'
    client.prices.assert_not_called()
    assert not (tmp_path / 'unused.db').exists()


@pytest.mark.parametrize('fields', [dict(lastPrice='nan'), dict(lastPrice='inf'), dict(lastPrice='0'),
    dict(currency='USD'), dict(timestamp='2026-10-30T09:00:00+09:00'),
    dict(timestamp='2026-10-28T15:30:00+09:00'), dict(timestamp='2026-10-30T08:20:00'), dict(timestamp='')])
def test_invalid_or_stale_prices_are_rejected(fields):
    with pytest.raises((ValueError, TypeError)):
        validate_mark({**rows()[0], **fields}, datetime(2026, 10, 30, 8, 30, tzinfo=KST))


def test_previous_trading_day_price_is_allowed_across_weekend():
    row = {**rows()[0], 'timestamp': '2026-10-30T19:59:00+09:00'}
    assert validate_mark(row, datetime(2026, 11, 2, 8, 30, tzinfo=KST))['price'] == 110


def test_new_entry_cannot_reuse_pre_entry_trade(tmp_path):
    path, profiles, store = account(tmp_path)
    with closing(data_store.connect(path)) as db, db:
        db.execute("UPDATE virtual_trades SET created_at='2026-10-30T08:25:00'")
    client = Mock(); client.prices.return_value = rows()
    refresh(datetime(2026, 10, 30, 8, 30, tzinfo=KST), profiles, client, store)
    marks, status = read_premarket(path, store)
    assert status['state'] == 'failed'
    state = value_saved_account(data_store.virtual_trader_state(path=path), [], [], marks)
    assert state['total_equity'] is None


def test_unreadable_sidecar_does_not_break_account_read(tmp_path):
    path = tmp_path / 'invalid.db'; path.write_text('invalid', encoding='utf-8')
    marks, status = read_premarket('unused', str(path))
    assert marks == []
    assert '확인 불가' in refresh_label(status)
