from datetime import date,datetime,timedelta
from unittest.mock import Mock,patch

import pytest

from stock_alarm import app,data_store,portfolio_risk,sell_check,dashboard_server
from stock_alarm.trading_profiles import PROFILES


def test_primary_sell_uses_actual_cost_and_keeps_unbought_tracking(monkeypatch):
    end=date(2026,10,29)
    bars=[[(end-timedelta(days=29-i)).strftime('%Y%m%d'),110,111,109,110,1000] for i in range(30)]
    monkeypatch.setattr(data_store,'virtual_trader_state',lambda **kw:{'holdings':[
        {'ticker':'A','name':'A','average_price':120.5,'quantity':10,'first_entry_at':'2026-10-28T14:00:00'}]})
    monkeypatch.setattr(data_store,'virtual_position_states',lambda **kw:{})
    monkeypatch.setattr(data_store,'query_rows',lambda *a,**kw:[])
    sell=Mock(return_value={});monkeypatch.setattr(data_store,'virtual_sell',sell)
    monkeypatch.setattr(sell_check,'naver_rows',lambda *a:bars)
    monkeypatch.setattr(sell_check,'stock_name',lambda ticker,name:name)
    monkeypatch.setattr('stock_alarm.toss_client.blocking_warnings_for',lambda ticker:[])
    monkeypatch.setattr(sell_check,'previous_returns',lambda:{})
    monkeypatch.setattr(sell_check,'max_returns',lambda:{})
    # A tracked full alert must not suppress a still-held or rebought virtual lot.
    monkeypatch.setattr(sell_check,'position_was_alerted',lambda *a,**kw:True)
    monkeypatch.setattr(sell_check,'write_log',lambda *a,**kw:None)
    monkeypatch.setattr(sell_check,'record_exit_events',lambda *a,**kw:None)
    monkeypatch.setenv('SELL_LOSS_PCT','5')
    tracked=[{'ticker':'A','name':'A','entry_price':'100','entry_date':'2026-10-28'}]
    alerts,_,_=sell_check._run_profile_sell_check(tracked,end,None,PROFILES['aggressive'])
    assert alerts[0].entry_price==120.5 and alerts[0].return_pct==pytest.approx((110/120.5-1)*100)
    assert alerts[0].stage=='' and '손절' in alerts[0].reason
    assert sell.call_args.args[0][0]['stage']==''
    tracked.append({'ticker':'B','name':'B','entry_price':'100','entry_date':'2026-10-28'})
    find=Mock(return_value=[]);monkeypatch.setattr(sell_check,'find_alerts',find)
    sell_check._run_profile_sell_check(tracked,end,None,PROFILES['aggressive'])
    assert [(p['ticker'],p['entry_price']) for p in find.call_args.args[0]]==[('A','120.5'),('B','100')]
    assert tracked[0]['entry_price']=='100'


def test_day_week_references_survive_later_observations_and_rollover(tmp_path,monkeypatch):
    path=str(tmp_path/'risk.db')
    monkeypatch.setenv('RISK_DAILY_LOSS_PCT','2');monkeypatch.setenv('RISK_WEEKLY_LOSS_PCT','5')
    def snapshot(equity,at):return portfolio_risk.snapshot({'total_equity':equity,'holdings_value':0},path,datetime.fromisoformat(at))
    snapshot(100000,'2026-08-28T15:30:00')
    first=snapshot(99000,'2026-08-31T09:00:00')
    second=snapshot(98000,'2026-08-31T09:05:00')
    assert first['daily_start_equity']==second['daily_start_equity']==100000
    assert second['daily_return_pct']==-2 and 'daily_loss_limit' in second['reason']
    next_day=snapshot(97000,'2026-09-01T09:00:00')
    assert next_day['daily_start_equity']==98000 and next_day['weekly_start_equity']==100000
    weekly=snapshot(94000,'2026-09-01T09:05:00')
    assert weekly['weekly_return_pct']==-6 and 'weekly_loss_limit' in weekly['reason']
    next_week=snapshot(95000,'2026-09-07T09:00:00')
    assert next_week['weekly_start_equity']==94000


def test_deposit_adjusts_references_without_becoming_profit_or_repeating(tmp_path):
    path=str(tmp_path/'risk.db')
    portfolio_risk.snapshot({'total_equity':100000,'holdings_value':0},path,datetime(2026,8,31,9))
    with data_store.connect(path) as db:
        db.execute("INSERT INTO virtual_deposits(amount,created_at) VALUES(900000,'2026-08-31T10:00:00')")
        db.commit()
    for minute in (1,2):
        value=portfolio_risk.snapshot({'total_equity':1000000,'holdings_value':0},path,datetime(2026,8,31,10,minute))
        assert value['daily_start_equity']==value['weekly_start_equity']==value['high_water']==1000000
        assert value['daily_return_pct']==value['weekly_return_pct']==0


def test_same_second_deposit_after_snapshot_is_not_lost(tmp_path):
    path=str(tmp_path/'risk.db');stamp=datetime(2026,8,31,9)
    portfolio_risk.snapshot({'total_equity':100000,'holdings_value':0},path,stamp)
    with data_store.connect(path) as db:
        db.execute('INSERT INTO virtual_deposits(amount,created_at) VALUES(900000,?)',(stamp.isoformat(timespec='seconds'),))
        db.commit()
    row=portfolio_risk.snapshot({'total_equity':1000000,'holdings_value':0},path,stamp+timedelta(seconds=1))
    assert row['daily_start_equity']==row['weekly_start_equity']==row['high_water']==1000000
    assert row['daily_return_pct']==row['weekly_return_pct']==0


def test_transaction_caps_duplicate_symbols_and_number_of_holdings(tmp_path,monkeypatch):
    path=str(tmp_path/'account.db')
    monkeypatch.setattr(portfolio_risk,'new_buys_allowed',lambda *a:(True,''))
    monkeypatch.setenv('VIRTUAL_TRADER_MAX_POSITION_PCT','30')
    data_store.virtual_deposit(100000,path)
    rows=[{'ticker':t,'close':100,'allocation_pct':30} for t in ('A','A','B','C')]
    result=data_store.virtual_buy(rows,path,max_holdings=2)
    assert [r['ticker'] for r in result['holdings']]==['A','B']
    assert all(r['cost']<=30000 for r in result['holdings'])


def test_holding_history_uses_actual_entry_basis(monkeypatch):
    monkeypatch.setattr(sell_check,'previous_returns',lambda:{})
    monkeypatch.setattr(sell_check,'max_returns',lambda:{})
    monkeypatch.setattr(data_store,'query_rows',lambda *a,**kw:[{'close':140},{'close':130}])
    check=Mock(return_value=None);monkeypatch.setattr(sell_check,'check_position',check)
    sell_check.find_alerts([{'ticker':'A','entry_price':'120','entry_date':'2026-10-28T14:00:00'}],
        date(2026,10,29),virtual_quantities={'A':10},history_path='not-used.db')
    assert check.call_args.args[2]==pytest.approx((130/120-1)*100)
    assert check.call_args.args[3]==pytest.approx((140/120-1)*100)


def test_profile_selection_stops_when_no_holding_room(monkeypatch):
    monkeypatch.setattr(app,'open_recommended_tickers',lambda:set())
    monkeypatch.setattr(data_store,'virtual_trader_state',lambda **kw:{'holdings':[{'ticker':'OLD'}]})
    evaluation=app.CandidateEvaluation('A','A',{},app.Pick('A','A',100,2,10**10,70))
    assert app.select_for_profile([evaluation],{**PROFILES['aggressive'],'max_holdings':1},5)==[]


def test_manual_buy_uses_selected_profile_and_shared_limits(monkeypatch):
    monkeypatch.setattr(app,'is_market_alert_time',lambda:True)
    query=Mock(return_value=[{'ticker':f'A{i}','name':f'A{i}'} for i in range(7)])
    monkeypatch.setattr(dashboard_server,'query_rows',query)
    prices=Mock();monkeypatch.setattr(app,'naver_rows',prices)
    def evaluate(ticker,name,*args,**kwargs):
        return app.CandidateEvaluation(ticker,name,{},app.Pick(ticker,name,100,2,10**10,70,atr20_pct=2))
    monkeypatch.setattr(app,'evaluate_naver_candidate',evaluate)
    buy=Mock(return_value={'bought':5});monkeypatch.setattr(app,'auto_buy_virtual_trader',buy)
    assert dashboard_server.manual_buy('neutral')=={'bought':5}
    assert query.call_args.args[1][0]=='neutral'
    options=buy.call_args.kwargs
    assert options['exposure_limit_override']==50 and options['max_position_pct_override']==20
    assert options['sector_cap_override']==30 and options['max_holdings_override']==10
    assert all(call.kwargs['max_cache_age_seconds']==60 for call in prices.call_args_list)
    monkeypatch.setattr(app,'is_market_alert_time',lambda:False)
    with pytest.raises(ValueError,match='장중'):dashboard_server.manual_buy('neutral')


def test_shared_buyer_respects_neutral_exposure_on_actual_temp_ledger(tmp_path,monkeypatch):
    path=str(tmp_path/'neutral.db')
    monkeypatch.setenv('VIRTUAL_TRADER_AUTO_BUY','1')
    monkeypatch.setattr(portfolio_risk,'new_buys_allowed',lambda *a,**kw:(True,''))
    monkeypatch.setattr(app,'correlation_limited_allocations',lambda p,a:a)
    monkeypatch.setattr(app,'sector_limited_allocations',lambda p,a,**kw:a)
    monkeypatch.setattr(app,'naver_market_up_ratio',lambda *a:.8)
    data_store.virtual_deposit(100000,path)
    picks=[app.Pick(f'A{i}',f'A{i}',100,2,10**10,70) for i in range(7)]
    result=app.buy_for_profile(picks,{**PROFILES['neutral'],'db_path':path})
    assert result['holdings_value']/result['total_equity']<=.5
    assert all(row['cost']/result['total_equity']<=.2 for row in result['holdings'])
