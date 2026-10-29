import hashlib
import json

from stock_alarm import research_data_validation as validation
from stock_alarm.research_data_collect import quote_row
from test_research_data_collect import NOW,QUOTE


def statement():
    rows=[{'rcept_no':'20260316000123','corp_code':'corp','bsns_year':'2025','reprt_code':'11011','account_id':account,'sj_div':section,'thstrm_amount':value,'currency':'KRW'}
          for account,section,value in [('ifrs-full_Assets','BS','1000'),('ifrs-full_CashFlowsFromUsedInOperatingActivities','CF','-10')]]
    raw=json.dumps(rows,ensure_ascii=False,sort_keys=True)
    return {'receipt':'20260316000123','year':2025,'report':'11011','first_seen_at':NOW.isoformat(),'payload':raw,'hash':hashlib.sha256(raw.encode()).hexdigest()}


def test_financial_proof_does_not_backdate_availability():
    row=statement();result=validation.check_statement(row)
    assert result['state']=='VALID_FORWARD_ONLY' and result['available_from']==row['first_seen_at']
    assert result['operating_cashflow']=='-10'
    assert validation.check_statement({**row,'hash':'bad'})['state']=='DATA_REQUIRED'
    assert validation.check_statement({**row,'first_seen_at':'2026-01-01'})['state']=='DATA_REQUIRED'


def test_quote_raw_values_and_quality_are_recomputed():
    values=quote_row('A',{**QUOTE,'currency':'KRW'},NOW)
    keys=['ticker','observed_at','quote_at','bid','ask','bid_quantity','ask_quantity','spread_pct','quality','venue','payload']
    row=dict(zip(keys,values))
    assert validation.check_quote(row)=='VALID_INTEGRATED_SNAPSHOT'
    assert validation.check_quote({**row,'ask':999})=='INVALID'


def test_missing_delisted_stock_is_detected_per_date():
    universe=[{'ticker':'A','status':'delisted','delisted_on':'2020-01-01'}, {'ticker':'B','status':'listed','delisted_on':''}]
    snapshots=[{'requested_date':'20191230','scope':'KOSPI','payload':json.dumps([{'종목코드':'B','업종명':'제조업'}])}]
    result=validation.check_industry(snapshots,universe,{'A':'20180101','B':'20180101'})
    assert result['coverage'][0]['missing']==['A']
    assert result['delisted_present']==0


def test_industry_coverage_uses_historical_market_membership():
    universe=[{'ticker':'A','status':'listed','delisted_on':''},{'ticker':'B','status':'listed','delisted_on':''}]
    snapshots=[{'requested_date':'20191230','scope':'KOSPI','payload':json.dumps([{'종목코드':'B','업종명':'제조업'}])}]
    result=validation.check_industry(snapshots,universe,{'A':'20180101','B':'20180101'},
                                    {'20191230':{'members':['B']}})
    assert result['coverage'][0]['missing']==[]
    assert result['coverage'][0]['outside_selected_markets']==['A']


def original_xml(currency='KRW',cash_end='2025-12-31'):
    return f'''<xbrl xmlns:i="urn:ifrs" xmlns:x="urn:xbrli">
      <context id="instant"><period><instant>2025-12-31</instant></period></context>
      <context id="duration"><period><startDate>2025-01-01</startDate><endDate>{cash_end}</endDate></period></context>
      <unit id="u"><measure>iso4217:{currency}</measure></unit>
      <i:Assets contextRef="instant" unitRef="u">1000</i:Assets>
      <i:CashFlowsFromUsedInOperatingActivities contextRef="duration" unitRef="u">-10</i:CashFlowsFromUsedInOperatingActivities>
      </xbrl>'''.encode()


def test_original_pair_requires_matching_currency_and_period():
    assets,cash=validation.financial_pair(json.loads(statement()['payload']))
    assert validation.xbrl_pair_matches(original_xml(),assets,cash,'2025-12-31')
    assert not validation.xbrl_pair_matches(original_xml(currency='USD'),assets,cash,'2025-12-31')
    assert not validation.xbrl_pair_matches(original_xml(cash_end='2024-12-31'),assets,cash,'2025-12-31')


def test_original_receipt_availability_is_after_publication_and_ends_at_correction(tmp_path,monkeypatch):
    import zipfile
    row=statement()
    filings=[{'rcept_no':row['receipt'],'report_nm':'사업보고서 (2025.12)','rcept_dt':'20260316'},
             {'rcept_no':'20260401000123','report_nm':'[기재정정]사업보고서 (2025.12)','rcept_dt':'20260401'}]
    monkeypatch.setattr('stock_alarm.research_data_collect.dart_json',lambda p:{'total_page':1,'list':filings})
    monkeypatch.setenv('DART_API_KEY','test')
    with zipfile.ZipFile(tmp_path/(row['receipt']+'_xbrl.zip'),'w') as z:z.writestr('entity_2025-12-31.xbrl',original_xml())
    result=validation.verify_original(row,tmp_path,['20260316','20260317','20260401','20260402'])
    assert result['state']=='CFO_ASSETS_ORIGINAL_MATCH'
    assert result['available_from_session']=='20260317'
    assert result['valid_until_session']=='20260402'
