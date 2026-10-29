import json
from datetime import datetime, timedelta
import sqlite3
import pytest

from stock_alarm.research_data_collect import quote_row
from stock_alarm.research_followup import pre_auction_exit, quote_cases, replay_closed, record_quote_cases, PROTOCOL


DAY='2026-10-30'
CALENDAR={'today':{'date':DAY,'integrated':{'regularMarket':{
    'startTime':DAY+'T09:00:00+09:00','singlePriceAuctionStartTime':DAY+'T15:20:00+09:00'}}}}


def quote(clock,bid=99):
    now=datetime.fromisoformat(DAY+'T'+clock+'+09:00')
    values=quote_row('A',{'currency':'KRW','timestamp':now.isoformat(),
        'bids':[{'price':bid,'volume':10}],'asks':[{'price':bid+1,'volume':10}]},now)
    return dict(zip(['ticker','observed_at','quote_at','bid','ask','bid_quantity','ask_quantity','spread_pct','quality','venue','payload'],values))


def test_exit_requires_new_quote_before_auction_and_does_not_retry_missed_decision():
    rows=[quote('15:10:00'),quote('15:10:20')]
    result=pre_auction_exit(rows,CALENDAR)
    assert result['net_proceeds']==99*.9985
    assert pre_auction_exit([quote('15:18:50'),quote('15:20:00')],CALENDAR) is None
    assert pre_auction_exit([quote('15:10:00'),quote('15:15:00'),quote('15:15:20')],CALENDAR) is None
    assert pre_auction_exit(rows,{}) is None


def test_quote_case_is_registered_only_forward_and_uses_ask_entry_bid_exit():
    signal={'ticker':'A','signal_day':DAY,'checked_at':DAY+'T10:00:00+09:00',
            'signal_json':json.dumps({'entry_earliest_at':DAY+'T09:59:00+09:00'})}
    rows=[quote('10:00:20'),quote('15:10:00',109),quote('15:10:20',109)]
    now=datetime.fromisoformat(DAY+'T15:18:00+09:00')
    cases=quote_cases([signal],rows,CALENDAR,DAY+'T09:00:00+09:00',now)
    assert cases[0]['state']=='SCENARIO_COMPLETE'
    assert abs(cases[0]['net_return_pct']-(109*.9985/(100*1.0015)-1)*100)<1e-9
    assert quote_cases([signal],rows,CALENDAR,DAY+'T10:01:00+09:00',now)==[]
    assert quote_cases([signal],rows[:1],CALENDAR,DAY+'T09:00:00+09:00',now)[0]['state']=='WAITING_EXIT_QUOTE'


def test_waiting_and_missing_are_distinct_and_future_quotes_are_not_used():
    signal={'ticker':'A','signal_day':DAY,'checked_at':DAY+'T10:00:00+09:00',
            'signal_json':json.dumps({'entry_earliest_at':DAY+'T09:59:00+09:00'})}
    def state(clock,quotes):
        return quote_cases([signal],quotes,CALENDAR,DAY+'T09:00:00+09:00',datetime.fromisoformat(DAY+'T'+clock+'+09:00'))[0]['state']
    assert state('10:00:10',[quote('10:00:20')])=='WAITING_ENTRY_QUOTE'
    assert state('10:01:01',[])=='ENTRY_DATA_MISSING'
    assert state('10:01:01',[quote('10:00:20')])=='WAITING_EXIT_WINDOW'
    assert state('15:20:00',[quote('10:00:20')])=='EXIT_DATA_MISSING'


def test_registered_inputs_and_finished_outcomes_are_frozen_and_idempotent():
    signal={'ticker':'A','signal_day':DAY,'checked_at':DAY+'T10:00:00+09:00',
            'signal_json':json.dumps({'entry_earliest_at':DAY+'T09:59:00+09:00'})}
    registry={'activated_at':DAY+'T09:00:00+09:00','rules':PROTOCOL}
    with sqlite3.connect(':memory:') as db:
        started=datetime.fromisoformat(DAY+'T10:01:00+09:00')
        first=record_quote_cases(db,[signal],[quote('10:00:20')],CALENDAR,registry,started)
        assert first[0]['state']=='WAITING_EXIT_WINDOW'
        changed={**signal,'checked_at':DAY+'T11:00:00+09:00'}
        rows=[quote('10:00:20'),quote('15:10:00',109),quote('15:10:20',109)]
        completed_at=datetime.fromisoformat(DAY+'T15:18:00+09:00')
        finished=record_quote_cases(db,[changed],rows,CALENDAR,registry,completed_at)
        assert finished[0]['state']=='SCENARIO_COMPLETE'
        assert finished[0]['decision_at']==signal['checked_at']
        assert record_quote_cases(db,[changed],[],{},registry,completed_at+timedelta(minutes=1))==finished
        assert db.execute('SELECT count(*) FROM quote_exit_cases').fetchone()[0]==1
        with pytest.raises(ValueError,match='clock moved backwards'):
            record_quote_cases(db,[signal],[],{},registry,started)


def test_changed_signal_version_stops_before_reading_or_registering_cases(monkeypatch):
    from stock_alarm.research_followup import run
    monkeypatch.setattr('stock_alarm.day_trade_research.VERSION','different-version')
    with pytest.raises(ValueError,match='Signal rule changed'):
        run('does-not-exist.db')


def test_historical_replay_is_separate_and_requires_intermediate_observations():
    buy={'trade_id':1,'ticker':'A','created_at':'2026-01-01T09:00:00','price':100,'quantity':10}
    checks=[{'ticker':'A','checked_at':'2026-01-02T10:00:00','close':106},
            {'ticker':'A','checked_at':'2026-01-03T10:00:00','close':110}]
    sales=[{'ticker':'A','created_at':'2026-01-05T10:00:00','price':90,'quantity':10}]
    result=replay_closed([buy],checks,sales)
    assert result['scope']=='HISTORICAL_EXPLORATORY_NOT_FORWARD_RESULTS'
    assert result['included']==1 and result['comparisons'][0]['mean_difference_pctp']>0
    assert replay_closed([buy],[],sales)['excluded']=={'NO_INTERMEDIATE_OBSERVATIONS':1}
