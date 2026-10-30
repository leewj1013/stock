from datetime import datetime
from unittest.mock import Mock,patch

import pytest

from stock_alarm import stock_events as events,market_sessions as sessions,app,sell_check

NOW=datetime(2026,10,30,9,5,tzinfo=sessions.KST)


def client(adjusted=100,raw=100,halt=False):
    c=Mock();c.stocks.return_value=[{'symbol':'A','status':'ACTIVE','isinCode':'OLD','koreanMarketDetail':{'krxTradingSuspended':halt,'nxtTradingSuspended':False}}]
    c.candles.side_effect=lambda *a,**k:{'candles':[{'timestamp':'2026-10-29T09:00:00+09:00','closePrice':str(adjusted if k['adjusted'] else raw)}]}
    return c


def holding():return {'A':{'ticker':'A','first_entry_at':'2026-10-28T09:00:00','average_price':200,'quantity':2}}


def test_halt_is_blocked_without_quantity_mutation():
    c=client(halt=True);held=holding()
    blocked=events.blocked(['A'],held,c,NOW)
    assert 'KRX' in ';'.join(blocked['A'])
    assert held['A']['quantity']==2


def test_basis_change_is_latched_until_matching_explicit_review():
    c=client(100,200)
    with patch('stock_alarm.stock_events.day_info',return_value={'open':True,'previous_date':'2026-10-29','end':NOW.replace(hour=15,minute=30)}):
        data=events.inspect(['A'],holding(),c,NOW)
        assert data['stocks']['A']['review_reasons']
        c.candles.side_effect=lambda *a,**k:{'candles':[{'timestamp':'2026-10-29T09:00:00+09:00','closePrice':'200'}]}
        data=events.inspect(['A'],holding(),c,NOW,force=True)
    item=data['stocks']['A']
    assert item['review_reasons']
    with pytest.raises(ValueError):events.acknowledge('A','wrong','checked')
    with pytest.raises(ValueError):events.acknowledge('A',item['review_fingerprint'],'')
    events.acknowledge('A',item['review_fingerprint'],'원가·수량 별도 확인 완료')
    assert not events.read_cache()['stocks']['A']['review_reasons']


def test_announcement_title_is_advisory_not_effective_date_or_hard_block():
    c=client()
    with patch('stock_alarm.stock_events.day_info',return_value={'open':True,'previous_date':'2026-10-29','end':NOW.replace(hour=15,minute=30)}):
        data=events.inspect(['A'],holding(),c,NOW,disclosures_fn=lambda *a,**k:[{'report_nm':'주식분할 결정','rcept_no':'receipt'}])
    assert data['stocks']['A']['advisories'][0]['receipt']=='receipt'
    assert not events.reasons('A',NOW,data,carried=True)


def test_isin_change_requires_review_even_when_price_is_unchanged():
    c=client();events.inspect(['A'],{},c,NOW)
    c.stocks.return_value[0]['isinCode']='NEW'
    data=events.inspect(['A'],{},c,NOW,force=True)
    assert any('식별번호' in r for r in data['stocks']['A']['review_reasons'])


def test_api_failure_cannot_erase_pending_review():
    c=client();events.inspect(['A'],{},c,NOW)
    data=events.read_cache();data['stocks']['A']['review_reasons']=['pending'];events.save_cache(data)
    c.stocks.side_effect=OSError('secret-not-stored')
    data=events.inspect(['A'],{},c,NOW,force=True)
    assert 'pending' in events.reasons('A',NOW,data)
    assert 'secret-not-stored' not in str(data)


def test_buy_gate_filters_before_allocation_and_sell_gate_excludes_held_stock():
    pick=app.Pick('A','A',100,0,0,0)
    with patch('stock_alarm.data_store.virtual_trader_state',return_value={'cash':1000,'holdings':[]}),patch('stock_alarm.stock_events.blocked',return_value={'A':['halt']}),patch.dict('os.environ',{'VIRTUAL_TRADER_AUTO_BUY':'1'}):
        assert app.auto_buy_virtual_trader([pick]) is None
    state={'cash':100,'holdings':[{'ticker':'A','average_price':200,'quantity':2,'first_entry_at':'2026-10-28'}]}
    profile={'db_path':'unused','sell_alerts_log':'unused','sell_policy':{}}
    with patch('stock_alarm.data_store.virtual_trader_state',return_value=state),patch('stock_alarm.stock_events.blocked',return_value={'A':['halt']}),patch('stock_alarm.sell_check.find_alerts',return_value=[]) as alerts,patch('stock_alarm.data_store.virtual_position_states',return_value={}),patch('stock_alarm.data_store.virtual_sell',return_value={}):
        sell_check._run_profile_sell_check([],NOW.date(),None,profile)
        assert alerts.call_args.args[0]==[]


def test_latched_review_survives_stale_writer_and_preserves_original_evidence():
    c=client(100,200)
    with patch('stock_alarm.stock_events.day_info',return_value={'open':True,'previous_date':'2026-10-29','end':NOW.replace(hour=15,minute=30)}):
        data=events.inspect(['A'],holding(),c,NOW)
    original=data['stocks']['A']['review_events'][0]
    stale={'stocks':{'A':{'checked_at':'2026-10-29T09:00:00+09:00','review_reasons':[]}}}
    events.save_cache(stale)
    current=events.read_cache()['stocks']['A']
    assert current['review_reasons']
    assert current['review_events'][0]==original
    assert original['raw_close']==200


def test_nxt_only_halt_does_not_suspend_krx_strategy():
    c=client();c.stocks.return_value[0]['koreanMarketDetail']['nxtTradingSuspended']=True
    assert events.blocked(['A'],{},c,NOW)=={}


def test_real_price_loss_is_not_mistaken_for_share_adjustment():
    c=client(200,200);held=holding();held['A']['average_price']=1000
    with patch('stock_alarm.stock_events.day_info',return_value={'open':True,'previous_date':'2026-10-29','end':NOW.replace(hour=15,minute=30)}):
        assert events.blocked(['A'],held,c,NOW)=={}
