import json
from datetime import datetime, timedelta

from stock_alarm.research_input_gate import announcement_at, balance_available, industry_rows, quote_fill
from stock_alarm.research_data_collect import quote_row


NOW=datetime.fromisoformat('2026-10-29T10:00:00+09:00')


def test_industry_missing_and_conflicting_rows_are_never_filled():
    snapshots=[{'requested_date':'20200131','observed_at':NOW.isoformat(),'payload':json.dumps([
        {'종목코드':'A','업종명':'금융'},{'종목코드':'B','업종명':'제조'},
        {'종목코드':'C','업종명':'제조'},{'종목코드':'C','업종명':'금융'}])}]
    rows=industry_rows(snapshots,[{'date':'20200131','missing':['A']}])
    assert [r['ticker'] for r in rows]==['B']
    assert not rows[0]['historical_eligible']


def test_announcements_never_use_later_corrections_or_ignore_visible_withdrawals():
    original={'receipt':'1','title':'결산실적공시예고','expected_date':'2026-11-06','timing':'UNSPECIFIED','first_seen_at':NOW.isoformat()}
    correction={**original,'receipt':'2','title':'[정정]결산실적공시예고','first_seen_at':(NOW+timedelta(days=1)).isoformat()}
    assert announcement_at([original,correction],NOW)['state']=='DATE_KNOWN_TIME_UNSPECIFIED'
    assert announcement_at([original,correction],NOW+timedelta(days=1))['state']=='CORRECTION_REVIEW_REQUIRED'
    assert announcement_at([{**original,'title':'[철회]결산실적공시예고'}],NOW)['state']=='CORRECTION_REVIEW_REQUIRED'
    assert announcement_at([original],NOW-timedelta(seconds=1))['state']=='NO_KNOWN_EVENT'


def test_explicit_correction_link_replaces_only_the_same_period_after_first_seen():
    doc='1. 회사명 회사 2. 결산대상기간 시작일 2026-01-01 종료일 2026-06-30 3. 결산실적 공시예정일 '
    original={'ticker':'A','receipt':'20261022000123','published_date':'20261022','title':'결산실적공시예고',
              'expected_date':'2026-11-01','timing':'UNSPECIFIED','first_seen_at':NOW.isoformat(),
              'payload':json.dumps({'document':doc+'2026-11-01'})}
    correction={**original,'receipt':'20261027000123','published_date':'20261027','title':'[정정]결산실적공시예고',
                'first_seen_at':(NOW+timedelta(days=1)).isoformat(),
                'payload':json.dumps({'document':'정정신고 2. 정정관련 공시서류제출일 2026-10-22 '+doc+'2026-11-04'})}
    assert announcement_at([original,correction],NOW)['expected_date']=='2026-11-01'
    result=announcement_at([original,correction],NOW+timedelta(days=1))
    assert result['expected_date']=='2026-11-04' and result['receipts']==[correction['receipt']]
    flagged={**original,'payload':json.dumps({'document':doc+'2026-11-01','filing':{'rm':'유정'}})}
    assert announcement_at([flagged],NOW)['state']=='CORRECTION_REVIEW_REQUIRED'
    assert announcement_at([flagged,correction],NOW+timedelta(days=1))['expected_date']=='2026-11-04'
    mismatch={**correction,'payload':correction['payload'].replace('2026-06-30','2026-09-30')}
    assert announcement_at([original,mismatch],NOW+timedelta(days=1))['state']=='CORRECTION_REVIEW_REQUIRED'


def test_withdrawal_flag_without_changed_title_is_not_missed_or_backdated():
    original={'receipt':'1','title':'결산실적공시예고','expected_date':'2026-11-06','timing':'UNSPECIFIED',
              'first_seen_at':NOW.isoformat(),'payload':json.dumps({'filing':{'rm':'유'}})}
    withdrawn={**original,'first_seen_at':(NOW+timedelta(hours=1)).isoformat(),
               'payload':json.dumps({'filing':{'rm':'유철'}})}
    assert announcement_at([original,withdrawn],NOW)['state']=='DATE_KNOWN_TIME_UNSPECIFIED'
    assert announcement_at([original,withdrawn],NOW+timedelta(hours=1))['state']=='CORRECTION_REVIEW_REQUIRED'
    same_time={**withdrawn,'first_seen_at':original['first_seen_at']}
    assert announcement_at([same_time,original],NOW)['state']=='CORRECTION_REVIEW_REQUIRED'
    assert announcement_at([{**original,'payload':'[]'}],NOW)['state']=='CORRECTION_REVIEW_REQUIRED'
    body_only={**withdrawn,'title':'[기재정정]결산실적공시예고',
               'payload':json.dumps({'filing':{'rm':'유'},'document':'3. 정정사유 실적 발표 계획 취소 4. 정정사항'})}
    assert announcement_at([original,body_only],NOW+timedelta(hours=1))['state']=='CORRECTION_REVIEW_REQUIRED'


def test_balance_waits_for_actual_observation_and_two_sessions():
    days=['20261023','20261026','20261027','20261028','20261029']
    assert balance_available('20261023','2026-10-26T10:00:00+09:00',days)=='2026-10-27T18:00:00+09:00'
    assert balance_available('20261023',NOW.isoformat(),days)==NOW.isoformat()
    assert balance_available('20261029',NOW.isoformat(),days) is None


def test_quote_scenario_requires_next_observation_session_depth_and_costs():
    payload={'currency':'KRW','timestamp':NOW.isoformat(),'asks':[{'price':101,'volume':20}], 'bids':[{'price':99,'volume':30}]}
    values=quote_row('A',payload,NOW)
    keys=['ticker','observed_at','quote_at','bid','ask','bid_quantity','ask_quantity','spread_pct','quality','venue','payload']
    row=dict(zip(keys,values));calendar={'today':{'date':'2026-10-29','integrated':{'regularMarket':{
        'startTime':'2026-10-29T09:00:00+09:00','singlePriceAuctionStartTime':'2026-10-29T15:20:00+09:00'}}}}
    assert quote_fill(row,'buy',10,NOW-timedelta(seconds=10),calendar)==101*1.0015*10
    assert quote_fill(row,'sell',10,NOW-timedelta(seconds=10),calendar)==99*.9985*10
    assert quote_fill(row,'buy',21,NOW-timedelta(seconds=10),calendar) is None
    assert quote_fill(row,'buy',1,NOW,calendar) is None
    assert quote_fill(row,'buy',1,NOW-timedelta(seconds=61),calendar) is None
    assert quote_fill(row,'buy',1,NOW-timedelta(seconds=10),{}) is None


def test_run_writes_exclusion_input_and_keeps_historical_rows_disabled(tmp_path,monkeypatch):
    from stock_alarm.research_data_collect import connect
    from stock_alarm.research_input_gate import run
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('stock_alarm.krx_research_collect.saved_sessions',lambda:set())
    reports=tmp_path/'reports';reports.mkdir()
    (reports/'research_data_validation.json').write_text(json.dumps({'industry':{'coverage':[{'date':'20200131','missing':['A']}]}}),encoding='utf-8')
    with connect('source.db') as db:
        db.execute('INSERT INTO krx_snapshots VALUES(?,?,?,?,?)',('industry','20200131','KOSPI',NOW.isoformat(),
            json.dumps([{'종목코드':'A','업종명':'금융'},{'종목코드':'B','업종명':'제조'}])))
    result=run('source.db')
    assert result['industry']['excluded_pairs']==1
    assert result['industry']['forward_rows']==1
    assert result['industry']['historical_eligible_rows']==0
    assert (reports/'research_priority/industry_exclusions.csv').exists()
    assert 'B' in (reports/'research_priority/industry_forward_input.csv').read_text(encoding='utf-8-sig')
