import json
from datetime import datetime,timedelta
from unittest.mock import Mock,patch

from stock_alarm import market_sessions as sessions
from stock_alarm import run_gate,premarket_valuation,app


def calendar(start=9,end=15,closed=False):
    day='2026-10-30'
    return {'today':{'date':day,'integrated':None if closed else {
        'preMarket':None,
        'regularMarket':{'startTime':f'{day}T{start:02}:00:00+09:00','endTime':f'{day}T{end:02}:30:00+09:00'}}},
        'previousBusinessDay':{'date':'2026-10-29'},'nextBusinessDay':{'date':'2026-11-02'}}


def set_calendar(start=9,end=15,closed=False):
    client=Mock();client.market_calendar_kr.return_value=calendar(start,end,closed)
    sessions.refresh(datetime(2026,10,30,8,tzinfo=sessions.KST),client,force=True)
    return client


def test_shifted_open_blocks_early_trades_and_allows_late_close():
    set_calendar(10,16)
    with patch('stock_alarm.app.is_trading_day',return_value=True):
        assert not app.is_market_alert_time(datetime(2026,10,30,9,30))
        assert app.is_market_alert_time(datetime(2026,10,30,10,1))
        assert app.is_market_alert_time(datetime(2026,10,30,16,20))
    assert run_gate.should_run('intraday',datetime(2026,10,30,9,30))
    assert run_gate.should_run('sell',datetime(2026,10,30,16,39))
    assert not run_gate.should_run('sell',datetime(2026,10,30,16,41))
    assert not run_gate.should_run('daily',datetime(2026,10,30,16,0))
    assert sessions.close_wait_seconds(datetime(2026,10,30,16,tzinfo=sessions.KST))==3600
    assert '시간 변경일' in sessions.label(datetime(2026,10,30,9,tzinfo=sessions.KST))


def test_shifted_preopen_refresh_window_and_deadline():
    set_calendar(10,16)
    assert not premarket_valuation.in_refresh_window(datetime(2026,10,30,8,30,tzinfo=sessions.KST))
    assert premarket_valuation.in_refresh_window(datetime(2026,10,30,9,30,tzinfo=sessions.KST))
    assert premarket_valuation.refresh_check({},datetime(2026,10,30,9,32,tzinfo=sessions.KST))['run_state']=='pending'
    assert premarket_valuation.refresh_check({},datetime(2026,10,30,9,36,tzinfo=sessions.KST))['run_state']=='missed'


def test_temporary_holiday_overrides_weekday_static_calendar():
    set_calendar(closed=True)
    assert not run_gate.should_run('open',datetime(2026,10,30,8,30))
    assert not run_gate.should_run('intraday',datetime(2026,10,30,10))
    assert not app.is_market_alert_time(datetime(2026,10,30,10))
    assert '휴장' in sessions.label(datetime(2026,10,30,10,tzinfo=sessions.KST))


def test_calendar_failure_does_not_replace_today_good_cache():
    client=set_calendar()
    before=sessions.CACHE_PATH.read_bytes()
    client.market_calendar_kr.side_effect=OSError('offline')
    sessions.refresh(datetime(2026,10,30,9,tzinfo=sessions.KST),client,force=True)
    assert sessions.CACHE_PATH.read_bytes()==before
    assert sessions.day_info(datetime(2026,10,30,9,tzinfo=sessions.KST))['open']
    assert sessions.day_info(datetime(2026,11,2,9,tzinfo=sessions.KST)) is None


def test_malformed_calendar_and_configured_missing_calendar_fail_closed(monkeypatch):
    monkeypatch.setenv('TOSS_CLIENT_ID','test');monkeypatch.setenv('TOSS_CLIENT_SECRET','test')
    client=Mock();client.market_calendar_kr.return_value=calendar();client.market_calendar_kr.return_value['today']['integrated']['regularMarket']['startTime']='invalid'
    sessions.refresh(datetime(2026,10,30,9,tzinfo=sessions.KST),client)
    assert not sessions.regular_allowed(datetime(2026,10,30,9,tzinfo=sessions.KST))


def test_normal_pre_market_auction_cutoff_is_not_continuous_trading():
    client=Mock();data=calendar();data['today']['integrated']['preMarket']={
        'startTime':'2026-10-30T08:00:00+09:00','singlePriceAuctionStartTime':'2026-10-30T08:50:00+09:00','endTime':'2026-10-30T09:00:00+09:00'}
    client.market_calendar_kr.return_value=data;sessions.refresh(datetime(2026,10,30,8,tzinfo=sessions.KST),client)
    assert premarket_valuation.in_refresh_window(datetime(2026,10,30,8,30,tzinfo=sessions.KST))
    assert not premarket_valuation.in_refresh_window(datetime(2026,10,30,8,55,tzinfo=sessions.KST))


def test_preopen_evaluation_cannot_write_regular_risk_baseline():
    from stock_alarm import virtual_trader_report
    set_calendar(10,16)
    with patch('stock_alarm.virtual_trader_report.load_env'),patch('stock_alarm.virtual_trader_report.datetime') as clock,patch('stock_alarm.virtual_trader_report.record_virtual_valuation') as valuation:
        clock.now.return_value=datetime(2026,10,30,9,30,tzinfo=sessions.KST)
        assert virtual_trader_report.run()['status']=='preopen_display_only'
        valuation.assert_not_called()
