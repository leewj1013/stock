from datetime import datetime
from unittest.mock import Mock

import pytest

from stock_alarm import research_data_collect as collector

NOW=datetime.fromisoformat("2026-10-29T10:00:00+09:00")
QUOTE={"timestamp":NOW.isoformat(),"asks":[{"price":"103","volume":"1"},{"price":"101","volume":"20"}],
       "bids":[{"price":"99","volume":"30"}]}


def test_best_quotes_are_sorted_and_source_venue_is_not_invented():
    row=collector.quote_row("A",QUOTE,NOW)
    assert row[3:7] == (99,101,30,20)
    assert row[7] == 2 and row[8:10] == ("FRESH","UNSPECIFIED")
    assert collector.quote_row("A",{**QUOTE,"timestamp":None},NOW)[8] == "MISSING_TIMESTAMP"


def test_invalid_and_crossed_quotes_are_rejected():
    with pytest.raises(ValueError):collector.quote_row("A",{},NOW)
    with pytest.raises(ValueError):collector.quote_row("A",{**QUOTE,"bids":[{"price":"104","volume":"1"}]},NOW)
    with pytest.raises(ValueError):collector.quote_row("A",{**QUOTE,"asks":[{"price":"nan","volume":"1"}]},NOW)


def test_quote_collection_is_append_only_and_failure_is_visible(tmp_path):
    path=str(tmp_path/"data.db");client=Mock()
    client.orderbook.side_effect=[QUOTE,ValueError("invalid")]
    assert collector.collect_quotes(["A","B"],path,client,NOW)=={"added":1,"failures":1}
    with collector.connect(path) as db:
        assert db.execute("select count(*) from quotes").fetchone()[0] == 1
        assert db.execute("select state from collection_status where ticker='B'").fetchone()[0] == "ERROR"


def test_quote_collection_archives_official_calendar_once_per_day(tmp_path):
    path=str(tmp_path/'data.db');client=Mock()
    client.market_calendar_kr.return_value={'today':{'date':'2026-10-29','integrated':{'regularMarket':{}}}}
    client.orderbook.return_value=QUOTE
    collector.collect_quotes(['A'],path,client,NOW)
    collector.collect_quotes(['A'],path,client,NOW)
    assert client.market_calendar_kr.call_count==1
    with collector.connect(path) as db:
        assert db.execute("select state from collection_status where kind='calendar'").fetchone()[0]=='COLLECTED'


def test_regular_quote_job_records_two_rounds_and_preserves_failures(monkeypatch):
    class Clock:
        @staticmethod
        def now(zone):return NOW
    monkeypatch.setattr(collector,'datetime',Clock)
    monkeypatch.setattr(collector,'targets',lambda source,now:['A','B'])
    collect=Mock(side_effect=[{'added':2,'failures':0},{'added':1,'failures':1}])
    monkeypatch.setattr(collector,'collect_quotes',collect)
    assert collector.run_quotes('source','data')=={'added':3,'failures':1,'observation_rounds':2}
    assert collect.call_count==2
    assert all(call.args==(['A','B'],) and call.kwargs=={'path':'data'} for call in collect.call_args_list)


def test_expected_date_comes_from_explicit_label_not_financial_period():
    text='<td>결산대상기간</td><td>2026-01-01</td><td>결산실적 공시예정일</td><td>2026-11-12</td><td>정규장 마감 이후 발표</td>'
    assert collector.announcement_date(text)==("2026-11-12","AFTER_CLOSE")
    assert collector.announcement_date('<td>2026-11-12 발표</td>')==(None,"UNSPECIFIED")


@pytest.mark.parametrize('old,new,submitted', [('2026-07-28','2026-08-04','2026-07-22'),('2026-02-09','2026-02-04','2026-01-26')])
def test_real_correction_layout_uses_main_body_not_before_column(old,new,submitted):
    # DART 20260727800617 / 20260130900232: correction table precedes updated body.
    doc=f'''정정신고(보고) 2. 정정관련 공시서류제출일 {submitted}
        정정항목 정정전 정정후 결산실적 공시예정일 {old} {new}
        결산실적공시 예고 1. 회사명 회사 2. 결산대상기간 시작일 2025-01-01 종료일 2025-12-31
        3. 결산실적 공시예정일 {new}'''
    details=collector.announcement_details(doc)
    assert details['expected_date']==new
    assert details['previous_filing_date']==submitted
    assert details['period']==['2025-01-01','2025-12-31']
    assert collector.announcement_date(doc.split('1. 회사명')[0])==(None,'UNSPECIFIED')


def test_statement_receipt_versions_are_preserved_without_overwriting(tmp_path):
    path=str(tmp_path/"data.db")
    rows=[{"rcept_no":"20260316000123","sj_div":"CF","thstrm_amount":"100"}]
    with collector.connect(path) as db:
        assert collector.archive_statement(db,"A",2025,"11011","CFS",rows,NOW)==1
        assert collector.archive_statement(db,"A",2025,"11011","CFS",rows,NOW)==0
        changed=[{**rows[0],"thstrm_amount":"200"}]
        assert collector.archive_statement(db,"A",2025,"11011","CFS",changed,NOW)==1
        assert db.execute("select count(*) from statements").fetchone()[0]==2
        with pytest.raises(ValueError):collector.archive_statement(db,"A",2025,"11011","CFS",[{}],NOW)


def test_announcement_first_seen_is_not_backdated_and_history_is_excluded(tmp_path,monkeypatch):
    filings=[{"rcept_no":"20261023000123","report_nm":"결산실적공시 예고","rcept_dt":"20261023"},
             {"rcept_no":"20260923000123","report_nm":"결산실적공시 예고","rcept_dt":"20260923"}]
    monkeypatch.setattr(collector,"dart_json",lambda params:{"status":"000","list":filings,"total_page":1})
    monkeypatch.setattr(collector,"dart_document",lambda receipt:'<td>결산실적 공시예정일</td><td>'+('2026-11-12' if receipt.startswith('202610') else '2026-10-01')+'</td>')
    with collector.connect(str(tmp_path/"data.db")) as db:
        assert collector.collect_announcements(db,"A","corp",NOW)==2
        assert collector.collect_announcements(db,"A","corp",NOW)==0
        rows=db.execute("select state,first_seen_at from announcements order by receipt").fetchall()
        assert [r[0] for r in rows]==["HISTORICAL_ONLY","FUTURE_DATE_KNOWN"]
        assert all(r[1]==NOW.isoformat(timespec='seconds') for r in rows)


def test_existing_receipt_withdrawal_metadata_is_archived_without_refetching_body(tmp_path,monkeypatch):
    from datetime import timedelta
    filing={'rcept_no':'20261023000123','report_nm':'결산실적공시예고','rcept_dt':'20261023','rm':'유'}
    monkeypatch.setattr(collector,'dart_json',lambda params:{'list':[dict(filing)],'total_page':1})
    download=Mock(return_value='결산실적 공시예정일 2026-11-12')
    monkeypatch.setattr(collector,'dart_document',download)
    with collector.connect(str(tmp_path/'data.db')) as db:
        assert collector.collect_announcements(db,'A','corp',NOW)==1
        filing['rm']='유철'
        assert collector.collect_announcements(db,'A','corp',NOW+timedelta(hours=1))==1
        assert collector.collect_announcements(db,'A','corp',NOW+timedelta(hours=2))==0
        assert db.execute('select count(*) from announcements').fetchone()[0]==2
        assert db.execute("select count(*) from announcements where state='WITHDRAWN'").fetchone()[0]==1
    assert download.call_count==1


def test_withdrawal_evidence_accepts_real_document_paths_without_risk_prose_false_positive():
    assert collector.withdrawal_evidence({'report_nm':'증권신고서(채무증권)','rm':'철'})=='WITHDRAWN_FLAG'
    assert collector.withdrawal_evidence({'report_nm':'철 회 신 고 서','rm':''})=='WITHDRAWAL_DOCUMENT'
    # KCC DART 20250930800210: title/rm do not say withdrawal; structured correction reason does.
    filing={'report_nm':'[기재정정]수시공시의무관련사항(공정공시)','rm':'유'}
    doc='3. 정정사유 자기주식 활용계획 철회 안내 4. 정정사항 정정전 정정후'
    assert collector.withdrawal_evidence(filing,doc)=='WITHDRAWAL_CORRECTION_REVIEW'
    assert collector.withdrawal_evidence({'report_nm':'증권신고서'},'위험요인: 향후 청약을 철회할 수 있습니다.') is None


def test_real_ir_postponement_uses_updated_body_and_does_not_turn_delay_into_withdrawal():
    main='기업설명회(IR) 개최 1. 일시 및 장소 일시 {day} --:-- 장소 - 2. 참가 대상자 기관 3. 개최목적 2019년 연간 경영실적 발표 4. 개최방법 컨퍼런스콜'
    unknown=collector.ir_schedule_details('정정사유 기업설명회 지연 4. 정정사항 2020-02-04 '+main.format(day='-'))
    assert unknown['state']=='IR_DATE_UNCONFIRMED' and unknown['date'] is None and unknown['earnings_presentation']
    restored=collector.ir_schedule_details('정정사유 일정 확정 4. 정정사항 정정전 추후 재공시 '+main.format(day='2020-02-07'))
    assert restored['state']=='IR_SCHEDULED' and restored['date']=='2020-02-07'
    cancelled=collector.ir_schedule_details('정정사유 기업설명회 개최 취소 4. 정정사항 '+main.format(day='-'))
    assert cancelled['state']=='IR_CANCELLED' and cancelled['date'] is None
    conflicting=collector.ir_schedule_details('정정사유 기업설명회 개최 취소 4. 정정사항 '+main.format(day='2020-02-07'))
    assert conflicting['state']=='IR_REVIEW_REQUIRED'


def test_financial_backfill_is_bounded_and_resumes_without_refetching(tmp_path,monkeypatch):
    import sqlite3
    path=str(tmp_path/'data.db');source=str(tmp_path/'source.db')
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE financial_statement_lines(ticker TEXT,bsns_year INTEGER,reprt_code TEXT,fs_div TEXT,sj_div TEXT)')
        db.executemany('INSERT INTO financial_statement_lines VALUES(?,?,?,?,?)', [('A',2025,'11011','CFS','CF'),('B',2025,'11011','CFS','CF')])
    monkeypatch.setenv('DART_API_KEY','test')
    monkeypatch.setattr('stock_alarm.dart_reference.corp_code_by_stock',lambda t:'corp' if t=='A' else '')
    fetch=Mock(return_value=[{'rcept_no':'20260316000123','sj_div':'CF'}])
    monkeypatch.setattr('stock_alarm.financial_statement_lines.fetch_report',fetch)
    assert collector.backfill_statements(path,source,limit=1)=={'added':1,'attempted':1,'failures':0}
    assert collector.backfill_statements(path,source,limit=1)=={'added':0,'attempted':1,'failures':0}
    assert collector.backfill_statements(path,source,limit=1)=={'added':0,'attempted':0,'failures':0}
    assert fetch.call_count==1
