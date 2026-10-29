from datetime import date,timedelta

import pytest

from stock_alarm import bear_account_research as research


def source():
    days=[(date(2026,1,1)+timedelta(days=i)).strftime('%Y%m%d') for i in range(63)]
    iso=lambda d:f'{d[:4]}-{d[4:6]}-{d[6:]}'
    return {'index':[[d,100] for d in days], 'start':days[60],'cutoff':days[62],
            'deposits':[{'created_at':iso(days[60])+'T00:00:00','amount':1000}],
            'buys':[{'trade_id':1,'ticker':'A','created_at':iso(days[60])+'T10:00:00','price':100,'quantity':5}],
            'sales':[],'prices':{'A':{d:[100,80] for d in days[60:]}}}


def test_deposit_unitization_preserves_return_and_drawdown():
    data=source();day=data['index'][61][0]
    data['deposits'].append({'created_at':f'{day[:4]}-{day[4:6]}-{day[6:]}T21:00:00','amount':9000})
    result=research.replay_account(data,'baseline',fee_bps=0)
    assert result['metrics']['equity']==9900
    assert result['metrics']['twr_pct']==pytest.approx(-10)
    assert result['metrics']['mdd_pct']==pytest.approx(-10)


def test_fee_cash_limit_and_partial_exit_rounding():
    data=source();data['deposits'][0]['amount']=500
    day=data['index'][61][0];iso=f'{day[:4]}-{day[4:6]}-{day[6:]}'
    data['sales']=[{'sale_id':1,'ticker':'A','created_at':iso+'T10:00:00','price':120,'quantity':2}]
    result=research.replay_account(data,'baseline')
    assert result['audit'][0]['filled']==4
    assert result['metrics']['cash_clipped_buys']==1
    assert result['daily'][-1]['holdings']['1']['quantity']==3  # floor(2*4/5)=1 sold
    assert all(r['cash']>=0 for r in result['daily'])


def test_bear_half_and_skip_use_previous_session_only():
    data=source();data['index'][59][1]=50;data['index'][60][1]=200
    half=research.replay_account(data,'half',fee_bps=0)
    skip=research.replay_account(data,'skip',fee_bps=0)
    assert half['audit'][0]['filled']==2
    assert skip['audit'][0]['filled']==0
    assert skip['metrics']['twr_pct']==0


def test_missing_held_close_is_not_forward_filled():
    data=source();del data['prices']['A'][data['index'][61][0]]
    with pytest.raises(ValueError,match='Missing price'):
        research.replay_account(data,'baseline')


def test_overlapping_lots_are_rejected():
    data=source();data['buys'].append({**data['buys'][0],'trade_id':2,'created_at':data['buys'][0]['created_at'].replace('10:00','11:00')})
    with pytest.raises(ValueError,match='overlaps'):
        research.assign_sales(data['buys'],[])


def test_recovery_budget_next_open_and_scheduled_close():
    data=source();data['buys']=[]
    start,entry,end=[r[0] for r in data['index'][60:]]
    data['prices']['B']={entry:[10,11],end:[12,12]}
    known=f'{start[:4]}-{start[4:6]}-{start[6:]}T16:00:00'
    windows=[{'recovery':start,'end':end,'budget':700,'signals':[
        {'ticker':'B','known_at':known,'entry_day':entry,'profile_score':80}]}]
    result=research.replay_account(data,'resume',fee_bps=0,recovery=windows)
    assert [r['kind'] for r in result['audit']]==['reentry','reentry_exit']
    assert result['audit'][0]['price']==10 and result['audit'][0]['cost']==100
    assert result['metrics']['equity']==1020
    assert result['daily'][-1]['holdings']=={}


def test_intraday_deposit_with_holdings_requires_valuation():
    data=source();day=data['index'][61][0]
    data['deposits'].append({'created_at':f'{day[:4]}-{day[4:6]}-{day[6:]}T12:00:00','amount':100})
    with pytest.raises(ValueError,match='Intraday deposit'):
        research.replay_account(data,'baseline')


def test_reentry_limit_also_blocks_later_original_buy():
    data=source();start,entry,end=[r[0] for r in data['index'][60:]]
    data['buys'][0]['created_at']=f'{entry[:4]}-{entry[4:6]}-{entry[6:]}T10:00:00'
    known=f'{start[:4]}-{start[4:6]}-{start[6:]}T16:00:00'
    signals=[]
    for i in range(7):
        ticker=f'B{i}';data['prices'][ticker]={entry:[10,11],end:[12,12]}
        signals.append({'ticker':ticker,'known_at':known,'entry_day':entry,'profile_score':80-i})
    windows=[{'recovery':start,'end':end,'budget':700,'signals':signals}]
    result=research.replay_account(data,'resume',fee_bps=0,recovery=windows)
    assert result['metrics']['maximum_held_names']==7
    assert result['metrics']['holding_limited_buys']==1
    assert result['metrics']['cash_clipped_buys']==0
    assert result['metrics']['equity']==1140


@pytest.mark.parametrize('mode',['baseline','half','skip','resume'])
def test_same_seven_name_limit_in_all_accounts(mode):
    data=source();original=data['buys'][0]
    data['buys']=[{**original,'trade_id':i+1,'ticker':f'T{i}','quantity':1,'price':10} for i in range(8)]
    data['prices']={f'T{i}':{day:[10,10] for day in data['prices']['A']} for i in range(8)}
    result=research.replay_account(data,mode,fee_bps=0)
    assert result['metrics']['maximum_held_names']==7
    assert result['metrics']['holding_limited_buys']==1
    raw=research.replay_account(data,'baseline',fee_bps=0,position_limit=None)
    assert raw['metrics']['maximum_held_names']==8


def test_known_signal_is_required_before_reentry():
    data=source();data['buys']=[]
    start,entry,end=[r[0] for r in data['index'][60:]]
    windows=[{'recovery':start,'end':end,'budget':700,'signals':[
        {'ticker':'A','known_at':'2027-01-01T00:00:00','entry_day':entry}]}]
    with pytest.raises(ValueError,match='precedes'):
        research.replay_account(data,'resume',recovery=windows)


def test_complete_replay_writes_reproducible_results_and_csv(tmp_path,monkeypatch):
    import json
    data=source();data.update(rules=research.RULES,windows=[],signals=[],price_issues=[],excluded_failed_signal_rows=0)
    data['buys'][0]['cost']=500
    monkeypatch.setattr(research,'collect_inputs',lambda _:data)
    result=research.run(directory=tmp_path)
    saved=json.loads((tmp_path/'inputs.json').read_text(encoding='utf-8'))
    assert result['source_sha256']==research.digest(saved)
    from pathlib import Path
    assert Path(result['artifact_directory'],'inputs.json').exists()
    assert result['gross_cash_reconciliation_error']==0
    assert len((tmp_path/'daily_accounts.csv').read_text(encoding='utf-8-sig').splitlines())==13
    from stock_alarm.profit_research import bear_account_dashboard_data
    assert bear_account_dashboard_data(tmp_path/'results.json')['rows'][0][0]=='기존 매수 기회'
    assert bear_account_dashboard_data(tmp_path/'absent.json')=={'available':False}
    previous=(tmp_path/'results.json').read_bytes()
    previous_inputs=(tmp_path/'inputs.json').read_bytes()
    del data['prices']['A'][data['index'][61][0]]
    with pytest.raises(ValueError,match='Missing price'):research.run(directory=tmp_path)
    assert (tmp_path/'results.json').read_bytes()==previous
    assert (tmp_path/'inputs.json').read_bytes()==previous_inputs


def test_cache_search_includes_older_files_needed_for_held_dates(tmp_path,monkeypatch):
    import json
    monkeypatch.chdir(tmp_path);folder=tmp_path/'.cache/naver';folder.mkdir(parents=True)
    for i in range(13):
        (folder/f'A-20260102-202601{10+i:02d}.json').write_text(json.dumps([['20260102',100,110,90,100,1]]))
    (folder/'A-20251201-20260101.json').write_text(json.dumps([['20260101',90,100,80,90,1]]))
    prices,_,_=research.cached_prices({'A'},'20260101','20260123')
    assert prices['A']['20260101']==[90,90]
