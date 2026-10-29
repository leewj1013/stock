"""Audit collected evidence without promoting it into trading/backtest inputs."""
from __future__ import annotations

import csv
import bisect
import contextlib
import hashlib
import io
import json
import re
import os
import sqlite3
import zipfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .research_data_collect import PATH, KST, quote_row

REPORT = "reports/research_data_validation.json"


def check_quote(row):
    try:
        observed=datetime.fromisoformat(row['observed_at'])
        payload=json.loads(row['payload'])
        calculated=quote_row(row['ticker'],payload,observed)
        equal=all(abs(float(row[k])-calculated[i])<=1e-9 for k,i in
                  [('bid',3),('ask',4),('bid_quantity',5),('ask_quantity',6),('spread_pct',7)])
        if not equal or row['quality']!=calculated[8]:return 'INVALID'
        if payload.get('currency')!='KRW':return 'UNSUPPORTED_CURRENCY'
        return 'VALID_INTEGRATED_SNAPSHOT' if calculated[8]=='FRESH' else 'EXCLUDED_TIMESTAMP'
    except (ValueError,TypeError,KeyError,InvalidOperation):return 'INVALID'


def amount(row):
    value=Decimal(str(row['thstrm_amount']).replace(',',''))
    if not value.is_finite():raise ValueError('Non-finite financial amount')
    return value


def financial_pair(rows):
    assets=[r for r in rows if r.get('account_id')=='ifrs-full_Assets' and r.get('sj_div')=='BS']
    cash=[r for r in rows if r.get('account_id')=='ifrs-full_CashFlowsFromUsedInOperatingActivities' and r.get('sj_div')=='CF']
    if len(assets)!=1 or len(cash)!=1:raise ValueError('Standard CFO/assets pair is ambiguous or missing')
    if amount(assets[0])<=0 or assets[0].get('currency')!=cash[0].get('currency'):raise ValueError('Invalid assets/currency')
    return assets[0],cash[0]


def check_statement(row):
    try:
        raw=row['payload'];rows=json.loads(raw)
        if hashlib.sha256(raw.encode()).hexdigest()!=row['hash']:raise ValueError('Hash mismatch')
        if not rows or not re.fullmatch(r'\d{14}',row['receipt']):raise ValueError('Missing receipt')
        if any(str(r.get('rcept_no'))!=row['receipt'] or str(r.get('bsns_year'))!=str(row['year']) or r.get('reprt_code')!=row['report'] for r in rows):
            raise ValueError('Receipt/period mismatch')
        published=datetime.strptime(row['receipt'][:8],'%Y%m%d').date()
        if published>datetime.fromisoformat(row['first_seen_at']).date():raise ValueError('Future receipt')
        assets,cash=financial_pair(rows)
        return {'state':'VALID_FORWARD_ONLY','available_from':row['first_seen_at'],
                'assets':str(amount(assets)),'operating_cashflow':str(amount(cash)),'currency':assets['currency']}
    except (ValueError,TypeError,KeyError,InvalidOperation) as error:
        return {'state':'DATA_REQUIRED','reason':str(error)}


def xbrl_pair_matches(raw,assets,cash,end_day):
    """Require equal period end, dimensions and currency for both original facts."""
    if len(raw)>50_000_000 or b'<!DOCTYPE' in raw.upper():raise ValueError('Unsafe/oversize XBRL')
    root=ET.fromstring(raw)
    local=lambda t:t.rsplit('}',1)[-1]
    contexts={};units={}
    for element in root:
        tag=local(element.tag)
        if tag=='context':
            ends=[e.text for e in element.iter() if local(e.tag) in ('instant','endDate')]
            starts=[e.text for e in element.iter() if local(e.tag)=='startDate']
            dims=tuple(sorted((e.get('dimension',''),e.text or '') for e in element.iter() if local(e.tag) in ('explicitMember','typedMember')))
            contexts[element.get('id')]=(ends[0] if ends else None,starts[0] if starts else None,dims)
        elif tag=='unit':units[element.get('id')]=[e.text.rsplit(':',1)[-1] for e in element.iter() if local(e.tag)=='measure' and e.text]
    candidates=defaultdict(list)
    for element in root:
        name=local(element.tag)
        if name not in ('Assets','CashFlowsFromUsedInOperatingActivities'):continue
        context=contexts.get(element.get('contextRef'))
        if not context or context[0]!=end_day or units.get(element.get('unitRef'))!=[assets['currency']]:continue
        try:value=Decimal(element.text or '')
        except InvalidOperation:continue
        if value==amount(assets if name=='Assets' else cash):candidates[name].append(context)
    return any(a[2]==c[2] and c[1] and 300<=(date.fromisoformat(c[0])-date.fromisoformat(c[1])).days<=370
               for a in candidates['Assets'] for c in candidates['CashFlowsFromUsedInOperatingActivities'])


def first_price_day(path):
    with path.open(encoding='utf-8-sig',newline='') as file:
        row=next(csv.DictReader(file),None)
    return row['date'].replace('-','') if row else None


def check_industry(snapshots,universe,price_starts,membership=None):
    by_day=defaultdict(dict);sectors=defaultdict(set);markets=defaultdict(set);errors=[]
    for snapshot in snapshots:
        day=snapshot['requested_date'];seen=set()
        for row in json.loads(snapshot['payload']):
            ticker=row.get('종목코드') or row.get('티커') or row.get('index')
            if not ticker or ticker in seen or not row.get('업종명'):
                errors.append([day,snapshot['scope'],ticker]);continue
            seen.add(ticker);by_day[day][ticker]=row['업종명'];sectors[ticker].add(row['업종명']);markets[ticker].add(snapshot['scope'])
    coverage=[]
    for day,observed in sorted(by_day.items()):
        expected={r['ticker'] for r in universe if price_starts.get(r['ticker'],'9999')<=day
                  and (not r.get('delisted_on') or day<r['delisted_on'].replace('-',''))}
        outside=[]
        if membership and day in membership:
            members=set(membership[day]['members'])
            outside=sorted(expected-members)
            expected &= members
        missing=sorted(expected-observed.keys())
        coverage.append({'date':day,'expected':len(expected),'covered':len(expected)-len(missing),'missing':missing,
                         'outside_selected_markets':outside,'membership_checked':bool(membership and day in membership)})
    dead={r['ticker'] for r in universe if r['status']=='delisted'}
    return {'snapshots':len(snapshots),'schema_errors':errors,'coverage':coverage,
            'delisted_total':len(dead),'delisted_present':len(dead & sectors.keys()),
            'industry_label_changes':sum(len(v)>1 for v in sectors.values()),
            'market_moves':sum(len(v)>1 for v in markets.values()),
            'missing_price_history':sum(r['ticker'] not in price_starts for r in universe)}


def next_session(day,sessions):
    index=bisect.bisect_right(sessions,day.replace('-',''))
    return sessions[index] if index<len(sessions) else None


def verify_original(row,out_dir,sessions):
    from .research_data_collect import dart_json
    rows=json.loads(row['payload']);assets,cash=financial_pair(rows)
    data=dart_json({'corp_code':rows[0]['corp_code'],'bgn_de':row['receipt'][:4]+'0101',
                    'end_de':datetime.now(KST).strftime('%Y%m%d'),'last_reprt_at':'N',
                    'pblntf_detail_ty':'A001','page_count':100})
    filings=data.get('list',[])
    filing=next((r for r in filings if r['rcept_no']==row['receipt']),None)
    if not filing or int(data.get('total_page') or 0)>1:
        return {'state':'RECEIPT_OR_HISTORY_INCOMPLETE'}
    base=lambda title:re.sub(r'^\[[^]]+\]','',re.sub(r'\s+','',title))
    chain=sorted([r for r in filings if base(r['report_nm'])==base(filing['report_nm'])],key=lambda r:r['rcept_no'])
    params={'crtfc_key':os.environ['DART_API_KEY'],'rcept_no':row['receipt'],'reprt_code':row['report']}
    target=out_dir/(row['receipt']+'_xbrl.zip')
    if target.exists():raw=target.read_bytes()
    else:
        with urllib.request.urlopen('https://opendart.fss.or.kr/api/fnlttXbrl.xml?'+urllib.parse.urlencode(params),timeout=25) as response:
            raw=response.read(30_000_001)
        if len(raw)>30_000_000:raise ValueError('Oversize XBRL archive')
        if not zipfile.is_zipfile(io.BytesIO(raw)):return {'state':'XBRL_NOT_AVAILABLE'}
        target.write_bytes(raw)
    matched=False;periods=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for file in archive.infolist():
            if not file.filename.lower().endswith('.xbrl') or file.file_size>50_000_000:continue
            end=re.search(r'(\d{4}-\d{2}-\d{2})\.xbrl$',file.filename)
            if not end:continue
            periods.append(end[1])
            matched=matched or xbrl_pair_matches(archive.read(file),assets,cash,end[1])
    successors=[f for f in chain if f['rcept_no']>row['receipt']]
    return {'state':'CFO_ASSETS_ORIGINAL_MATCH' if matched else 'ORIGINAL_PAIR_NOT_MATCHED',
            'receipt_title':filing['report_nm'],'chain':[{'receipt':f['rcept_no'],'title':f['report_nm'],'date':f['rcept_dt']} for f in chain],
            'available_from_session':next_session(filing['rcept_dt'],sessions) if matched else None,
            'valid_until_session':next_session(successors[0]['rcept_dt'],sessions) if successors else None,
            'xbrl_sha256':hashlib.sha256(raw).hexdigest(),'periods':periods,
            'verified_fields':['operating_cashflow','total_assets']}


def run(path=PATH,remote=False,report=REPORT):
    from .krx_research_collect import saved_sessions
    out_dir=Path(report).parent/'data_validation_sources';out_dir.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        quotes=[dict(r) for r in db.execute('SELECT * FROM quotes')]
        statements=[dict(r) for r in db.execute('SELECT * FROM statements ORDER BY ticker')]
        snapshots=[dict(r) for r in db.execute("SELECT * FROM krx_snapshots WHERE kind='industry'")]
        balances=[dict(r) for r in db.execute("SELECT * FROM krx_snapshots WHERE kind='short_balance'")]
        announcements=[dict(r) for r in db.execute('SELECT * FROM announcements')]
    with open('data/backtest_expanded/pit_universe/tickers.csv',encoding='utf-8-sig',newline='') as file:
        universe=list(csv.DictReader(file))
    starts={}
    for item in universe:
        p=Path('data/backtest_expanded/pit_universe/ohlcv')/(item['ticker']+'.csv')
        if p.exists():
            day=first_price_day(p)
            if day:starts[item['ticker']]=day
    sessions=sorted(saved_sessions())
    quote_counts=defaultdict(int)
    for row in quotes:quote_counts[check_quote(row)]+=1
    financial=[]
    for i,row in enumerate(statements):
        entry={'ticker':row['ticker'],'receipt':row['receipt'],**check_statement(row)}
        if remote and entry['state']=='VALID_FORWARD_ONLY':
            try:entry['original']=verify_original(row,out_dir,sessions)
            except Exception as error:entry['original']={'state':'VALIDATION_ERROR','error_type':type(error).__name__}
        financial.append(entry)
        if remote and (i+1)%10==0:print(json.dumps({'financial_checked':i+1,'total':len(statements)}),flush=True)
    balance_summary=[]
    for row in balances:
        records=json.loads(row['payload'])
        dates=[str(r.get('날짜') or r.get('date') or '')[:10] for r in records]
        balance_summary.append({'requested':row['requested_date'],'scope':row['scope'],'rows':len(records),
                                'first_basis_date':min(dates) if any(dates) else row['requested_date'],
                                'last_basis_date':max(dates) if any(dates) else row['requested_date'],
                                'available_from':row['observed_at'],'state':'FORWARD_ONLY_PUBLICATION_TIME_REQUIRED'})
    membership_path=out_dir/'industry_membership.json'
    membership=json.loads(membership_path.read_text(encoding='utf-8')) if membership_path.exists() else {}
    example_path=out_dir/'announcement_example.json'
    examples=json.loads(example_path.read_text(encoding='utf-8')) if example_path.exists() else []
    result={'checked_at':datetime.now(KST).isoformat(),'scope':'no_trading_rule_or_backtest_promotion',
            'quotes':{'counts':dict(quote_counts),'venue':'KRX_NXT_INTEGRATED','source':'https://openapi.tossinvest.com/openapi-docs/faq.md',
                      'use':'integrated_snapshot_spread_only_not_guaranteed_fills'},
            'statements':financial,'industry':check_industry(snapshots,universe,starts,membership),'balances':balance_summary,
            'announcements':{'production_records':len(announcements),'validation_examples':examples,
                             'state':'PROSPECTIVE_COHORT_AND_CORRECTION_REVIEW_REQUIRED'}}
    Path(report).parent.mkdir(parents=True,exist_ok=True)
    Path(report).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'quotes':dict(quote_counts),'statements':len(financial),'industry_snapshots':len(snapshots),
                      'missing_industry_observations':sum(len(r['missing']) for r in result['industry']['coverage'])},ensure_ascii=True))
    return result


if __name__=='__main__':
    import sys
    from .app import load_env
    load_env()
    run(remote='--remote' in sys.argv)
