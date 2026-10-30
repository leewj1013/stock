import copy
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from stock_alarm.account_valuation import value_saved_account
from stock_alarm import data_store, dashboard_server, dashboard_v2
from datetime import datetime, timezone, timedelta


def account():
    return {'cash': 100, 'deposited': 1100, 'realized_profit_loss': 0,
            'holdings': [{'ticker': 'A', 'name': 'A', 'quantity': 2, 'cost': 1000,
                          'average_price': 500, 'first_entry_at': '2026-10-29T10:00:00'}]}


def test_newest_saved_quality_beats_old_check_and_totals_reconcile():
    state = account()
    original = copy.deepcopy(state)
    result = value_saved_account(state,
        [{'ticker': 'A', 'checked_at': '2026-10-29T15:27:00', 'close': 550}],
        [{'ticker': 'A', 'created_at': '2026-10-29T15:35:00', 'close': 540, 'status': 'valid'}])
    assert result['holdings'][0]['current_price'] == 540
    assert result['holdings_value'] == 1080
    assert result['total_equity'] == 1180
    assert result['unrealized_profit_loss'] == 80
    assert result['total_profit_loss'] == 80
    assert result['total_return_pct'] == 7.27
    assert state == original


def test_newer_check_beats_quality_using_timezone_aware_order():
    result = value_saved_account(account(),
        [{'ticker': 'A', 'checked_at': '2026-10-29T07:00:00+00:00', 'close': 560}],
        [{'ticker': 'A', 'created_at': '2026-10-29T15:35:00', 'close': 540, 'status': 'valid'}])
    assert result['holdings'][0]['current_price'] == 560
    assert result['price_updated_at'] == '2026-10-29T07:00:00+00:00'


def test_invalid_or_pre_entry_prices_do_not_turn_cost_into_a_price():
    quality = [dict(ticker='A', created_at='2026-10-29T11:00:00', close=999, status='quarantined'),
               dict(ticker='A', created_at='2026-10-29T11:01:00', close=float('nan'), status='valid'),
               dict(ticker='A', created_at='2026-10-29T11:02:00', close=float('inf'), status='valid')]
    result = value_saved_account(account(),
        [{'ticker': 'A', 'checked_at': '2026-10-29T09:00:00', 'close': 550}], quality)
    assert result['cash'] == 100
    assert result['total_equity'] is None
    assert result['holdings_value'] is None
    assert result['total_return_pct'] is None
    assert result['holdings'][0]['profit_loss'] is None
    assert result['price_unavailable_tickers'] == ['A']


def test_cash_only_account_needs_no_price_and_reflects_new_deposit():
    result = value_saved_account({'cash': 1200, 'deposited': 1200, 'holdings': []}, [], [])
    assert result['total_equity'] == 1200
    assert result['total_return_pct'] == 0
    assert result['holdings_value'] == 0


def test_current_quantity_not_historic_snapshot_quantity_is_valued():
    state = account()
    state.update(cash=650)
    state['holdings'][0].update(quantity=1, cost=500)
    result = value_saved_account(state, [], [dict(ticker='A', created_at='2026-10-29T15:35:00', close=540, status='valid')])
    assert result['total_equity'] == 1190
    assert result['holdings'][0]['valuation'] == 540


def test_partial_missing_price_does_not_publish_partial_account_total():
    state = account()
    state['holdings'].append({**state['holdings'][0], 'ticker': 'B'})
    result = value_saved_account(state, [dict(ticker='A', checked_at='2026-10-29T15:35:00', close=550)], [])
    assert result['holdings'][0]['valuation'] == 1100
    assert result['total_equity'] is None
    assert result['price_unavailable_tickers'] == ['B']


def test_price_freshness_separates_preopen_and_previous_day_data():
    now = datetime(2026,10,30,8,30,tzinfo=timezone(timedelta(hours=9)))
    for stamp, label, age in [('2026-10-30T08:29:00+09:00','당일 장전가',1),
                               ('2026-10-29T20:00:00+09:00','이전 거래일 가격',750)]:
        marks=[dict(ticker='A',observed_at=now.isoformat(),price_at=stamp,price=550,source='Toss 현재가',venue='UNSPECIFIED')]
        h=value_saved_account(account(),[],[],marks,now=now)['holdings'][0]
        assert h['price_basis_kind']==label
        assert h['price_age_minutes']==age
        assert h['price_warning']==(age==750)


def test_price_delay_and_recovered_price_after_failed_preopen():
    now=datetime(2026,10,30,9,20,tzinfo=timezone(timedelta(hours=9)))
    status={'attempted_at':'2026-10-30T08:30:00+09:00','failed_tickers':['A']}
    result=value_saved_account(account(),[dict(ticker='A',checked_at='2026-10-30T09:00:00',close=550)],[],refresh_status=status,now=now)
    assert '갱신 지연' in result['holdings'][0]['price_status_label']
    assert '장전 갱신 실패' not in result['holdings'][0]['price_status_label']
    previous=value_saved_account(account(),[dict(ticker='A',checked_at='2026-10-29T15:30:00',close=550)],[],refresh_status=status,now=now)
    assert '장전 갱신 실패' in previous['holdings'][0]['price_status_label']


def test_v1_v2_share_totals_and_rows_despite_stale_snapshot_and_check():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / 'account.db')
        data_store.virtual_deposit(1100, path)
        with closing(data_store.connect(path)) as con, con:
            con.execute("UPDATE virtual_accounts SET cash=100")
            con.execute("""INSERT INTO virtual_trades(created_at,ticker,name,price,quantity,cost,allocation_pct,score)
                VALUES('2026-10-29T10:00:00','A','A',500,2,1000,100,0)""")
            con.execute("""INSERT INTO virtual_valuation_snapshots(created_at,cash,holdings_cost,valuation,equity,profit_loss,return_pct,return_change_pct)
                VALUES('2026-10-29T15:30:00',100,1000,1100,1200,100,10,0)""")
        data_store.record_price_quality(dict(ticker='A',created_at='2026-10-29T15:35:00',close=540,status='valid',source='test'),path)
        # An older check has a different price; neither UI may prefer it.
        checks = [dict(ticker='A', checked_at='2026-10-29T15:27:00',close=550,decision='HOLD')]
        profile = {**dashboard_server.PROFILES['aggressive'], 'db_path': path}
        with patch.dict(dashboard_server.PROFILES, {'aggressive': profile}), \
             patch('stock_alarm.dashboard_server.recent_position_checks',return_value=checks), \
             patch('stock_alarm.dashboard_v2.recent_position_checks',return_value=checks), \
             patch('stock_alarm.dashboard_server.load_sector_mapping',return_value={}), \
             patch('stock_alarm.dashboard_server.active_strategy_version',return_value={}), \
             patch('stock_alarm.screener.fundamentals_for',return_value={}), \
             patch('stock_alarm.toss_client.TossClient',side_effect=ValueError('offline')):
            v1 = dashboard_server.trader_payload()
            v2 = dashboard_v2._kr_account('aggressive','test')
            marks = [dict(ticker='A', observed_at='2026-10-30T08:30:00+09:00',
                          price_at='2026-10-29T19:59:00+09:00', price=560,
                          source='Toss 현재가', venue='UNSPECIFIED')]
            with patch('stock_alarm.dashboard_server.read_premarket',return_value=(marks, {})), \
                 patch('stock_alarm.dashboard_v2.read_premarket',return_value=(marks, {})):
                pre_v1 = dashboard_server.trader_payload()
                pre_v2 = dashboard_v2._kr_account('aggressive','test')
            assert pre_v1['total_equity'] == 1220
            assert pre_v2['asset'] == '₩1,220'
            assert pre_v2['rows'][0][5] == '₩560'
            assert '2026-10-29 19:59' in pre_v2['rows'][0][6]
        assert v1['total_equity'] == 1180
        assert v2['asset'] == '₩1,180'
        assert v2['cash'] == '₩100'
        assert v2['marketValue'] == '₩1,080'
        assert v2['unrealized'] == '₩+80'
        assert v2['returnPct'] == '+7.27%'
        assert v1['holdings'][0]['current_price'] == 540
        assert v2['rows'][0][5] == '₩540'
        assert v2['rows'][0][7] == '₩+80'
        assert v2['priceAsOf'] == '2026-10-29 15:35'
