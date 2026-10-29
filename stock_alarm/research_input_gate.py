"""Conservative research input checks; never changes orders or historical sources."""
from __future__ import annotations

import bisect
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from .research_data_collect import KST, announcement_details, withdrawal_evidence
from .research_data_validation import check_quote


def industry_rows(snapshots, exclusions):
    """Exact-date rows only; historical vintage is still unverified."""
    blocked={(r['date'],ticker) for r in exclusions for ticker in r['missing']}
    rows={};conflicts=set()
    for snapshot in snapshots:
        for item in json.loads(snapshot['payload']):
            ticker=item.get('종목코드') or item.get('티커') or item.get('index')
            key=(snapshot['requested_date'],ticker)
            sector=item.get('업종명')
            if not ticker or not sector or key in blocked:continue
            if key in rows and rows[key]['sector']!=sector:conflicts.add(key)
            rows[key]={'date':key[0],'ticker':ticker,'sector':sector,
                       'available_from':snapshot['observed_at'],'historical_eligible':False}
    return [row for key,row in sorted(rows.items()) if key not in conflicts]


def announcement_chain(rows):
    """Link only an explicit original filing date and identical reporting period."""
    parsed=[];links={};unresolved=[];latest={}
    for row in sorted(rows,key=lambda r:r['first_seen_at']):
        prior=latest.get(row['receipt'])
        if prior and prior['first_seen_at']==row['first_seen_at'] and prior.get('payload')!=row.get('payload'):
            unresolved.append(row['receipt'])
        latest[row['receipt']]=row
    for row in latest.values():
        item=dict(row)
        try:
            payload=json.loads(row.get('payload') or '{}')
            document=payload.get('document','')
            details=announcement_details(document) if document else {}
            item['_remarks']=payload.get('filing',{}).get('rm','')
            item['_withdrawal']=withdrawal_evidence({**payload.get('filing',{}),'report_nm':row['title']},document)
        except (ValueError,TypeError,AttributeError):
            details={};unresolved.append(row['receipt'])
        item['_details']=details
        if details:item.update(expected_date=details['expected_date'],timing=details['timing'])
        parsed.append(item)
    for item in parsed:
        title=item['title'];details=item['_details']
        if item.get('_withdrawal'):
            unresolved.append(item['receipt']);continue
        if '정정' not in title:continue
        reference=details.get('previous_filing_date');period=details.get('period')
        parents=[r for r in parsed if reference and period and r.get('ticker')==item.get('ticker')
                 and str(r.get('published_date','')).replace('-','')==reference.replace('-','')
                 and r['_details'].get('period')==period and r['receipt']<item['receipt']]
        if len(parents)==1:links[item['receipt']]=parents[0]['receipt']
        else:unresolved.append(item['receipt'])
    superseded=set(links.values())
    unresolved.extend(r['receipt'] for r in parsed if '정' in r.get('_remarks','') and r['receipt'] not in superseded)
    return [r for r in parsed if r['receipt'] not in superseded],links,unresolved


def announcement_at(rows, as_of):
    """Unlinked corrections/withdrawals block the ticker instead of guessing a chain."""
    if as_of.tzinfo is None:raise ValueError('Decision needs a timezone')
    visible=[r for r in rows if datetime.fromisoformat(r['first_seen_at'])<=as_of]
    if not visible:return {'state':'NO_KNOWN_EVENT'}
    visible,links,unresolved=announcement_chain(visible)
    if unresolved:
        return {'state':'CORRECTION_REVIEW_REQUIRED'}
    future=[r for r in visible if r.get('expected_date') and r['expected_date']>as_of.astimezone(KST).date().isoformat()]
    dates={r['expected_date'] for r in future}
    if not future:return {'state':'NO_FUTURE_EVENT','linked_corrections':links}
    if len(dates)!=1:return {'state':'AMBIGUOUS_EVENT'}
    return {'state':'DATE_KNOWN_TIME_UNSPECIFIED' if any(r['timing']=='UNSPECIFIED' for r in future) else 'DATE_KNOWN',
            'expected_date':next(iter(dates)),'receipts':sorted({r['receipt'] for r in future}),
            'linked_corrections':links}


def balance_available(basis_day, observed_at, sessions):
    """T+2 18:00 is a lower bound; first observation also gates revised values."""
    days=sorted(set(sessions));day=basis_day.replace('-','')
    index=bisect.bisect_left(days,day)
    if index>=len(days) or days[index]!=day or index+2>=len(days):return None
    deadline=datetime.strptime(days[index+2],'%Y%m%d').replace(hour=18,tzinfo=KST)
    seen=datetime.fromisoformat(observed_at)
    if seen.tzinfo is None:raise ValueError('Observation needs a timezone')
    return max(deadline,seen).isoformat()


def quote_fill(row, side, quantity, decision_at, calendar, fee_bps=15, slippage_bps=0):
    """Next observed top-of-book scenario, not proof of actual execution."""
    if side not in ('buy','sell') or not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<=0:
        raise ValueError('Invalid side/quantity')
    if not 0<=fee_bps<10000 or not 0<=slippage_bps<10000:raise ValueError('Invalid costs')
    if decision_at.tzinfo is None:raise ValueError('Decision needs a timezone')
    observed=datetime.fromisoformat(row['observed_at'])
    if observed<=decision_at or observed-decision_at>timedelta(seconds=60):return None
    if check_quote(row)!='VALID_INTEGRATED_SNAPSHOT':return None
    day=observed.astimezone(KST).date().isoformat()
    session=next((v for v in calendar.values() if isinstance(v,dict) and v.get('date')==day),None)
    regular=(session or {}).get('integrated',{}).get('regularMarket')
    if not regular:return None
    start=datetime.fromisoformat(regular['startTime'])+timedelta(minutes=1)
    end=datetime.fromisoformat(regular['singlePriceAuctionStartTime'])
    quoted=datetime.fromisoformat(row['quote_at'])
    if not (start<=observed<end and start<=decision_at<end and start<=quoted<end):return None
    price=float(row['ask' if side=='buy' else 'bid'])
    if float(row['ask_quantity' if side=='buy' else 'bid_quantity'])<quantity:return None
    direction=1 if side=='buy' else -1
    return price*(1+direction*slippage_bps/10000)*(1+direction*fee_bps/10000)*quantity


def run(path='data/research_data_v1.db', out='reports/research_priority'):
    from .krx_research_collect import saved_sessions
    destination=Path(out);destination.mkdir(parents=True,exist_ok=True)
    audit=json.loads(Path('reports/research_data_validation.json').read_text(encoding='utf-8'))
    calendar_path=Path('reports/data_validation_sources/calendar.json')
    calendar=json.loads(calendar_path.read_text(encoding='utf-8'))['payload'] if calendar_path.exists() else {}
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        snapshots=[dict(r) for r in db.execute("SELECT * FROM krx_snapshots WHERE kind='industry'")]
        balances=[dict(r) for r in db.execute("SELECT * FROM krx_snapshots WHERE kind='short_balance'")]
        events=[dict(r) for r in db.execute('SELECT * FROM announcements')]
        quotes=[dict(r) for r in db.execute('SELECT * FROM quotes ORDER BY observed_at')]
        for row in db.execute("SELECT details FROM collection_status WHERE kind='calendar' AND state='COLLECTED' ORDER BY checked_at"):
            for value in json.loads(row['details']).values():
                if isinstance(value,dict) and value.get('date'):calendar[value['date']]=value
    coverage=audit['industry']['coverage']
    exclusions=[{'date':r['date'],'ticker':ticker,'reason':'MISSING_HISTORICAL_INDUSTRY'} for r in coverage for ticker in r['missing']]
    with (destination/'industry_exclusions.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=['date','ticker','reason']);writer.writeheader();writer.writerows(exclusions)
    industry=industry_rows(snapshots,coverage)
    with (destination/'industry_forward_input.csv').open('w',encoding='utf-8-sig',newline='') as file:
        fields=['date','ticker','sector','available_from','historical_eligible']
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(industry)
    sessions=sorted(saved_sessions());available=[]
    for snapshot in balances:
        for row in json.loads(snapshot['payload']):
            day=str(row.get('날짜') or row.get('date') or snapshot['requested_date'])[:10]
            available.append(balance_available(day,snapshot['observed_at'],sessions))
    now=datetime.now(KST);by_ticker=defaultdict(list)
    for event in events:by_ticker[event['ticker']].append(event)
    event_states={ticker:announcement_at(rows,now) for ticker,rows in by_ticker.items()}
    pairs=0;last={};spreads=[];costs={slip:[] for slip in (0,5,10)}
    for row in quotes:
        if check_quote(row)=='VALID_INTEGRATED_SNAPSHOT':spreads.append(row['spread_pct'])
        previous=last.get(row['ticker'])
        if previous:
            decision=datetime.fromisoformat(previous['observed_at'])
            if (quote_fill(row,'buy',1,decision,calendar) is not None
                    and quote_fill(row,'sell',1,decision,calendar) is not None):
                pairs+=1
                for slip,values in costs.items():
                    buy=quote_fill(row,'buy',1,decision,calendar,slippage_bps=slip)
                    sell=quote_fill(row,'sell',1,decision,calendar,slippage_bps=slip)
                    values.append((sell/buy-1)*100)
        last[row['ticker']]=row
    result={'checked_at':now.isoformat(),'industry':{'excluded_pairs':len(exclusions),'forward_rows':len(industry),
            'historical_eligible_rows':0,'state':'EXCLUSIONS_ENFORCED_VINTAGE_NOT_PROMOTED'},
            'announcements':{'records':len(events),'ticker_states':event_states,'state':'PROSPECTIVE_COHORT_REQUIRED'},
            'short_balance':{'rows':len(available),'availability_checked':sum(v is not None for v in available),
                             'state':'FIRST_OBSERVATION_AND_T_PLUS_2_GATED'},
            'quotes':{'rows':len(quotes),'quality':dict(Counter(check_quote(r) for r in quotes)),
                      'spread_above_0_3_pct':sum(v>.3 for v in spreads),'valid_spread_samples':len(spreads),
                      'adjacent_one_share_scenario_pairs':pairs,'state':'SCENARIO_ONLY_NO_ATTRIBUTED_CLOSED_TRADES',
                      'fee_bps_each_side':15,'crossing_cost_sensitivity':[
                          {'slippage_bps_each_side':slip,'pairs':len(values),
                           'mean_same_snapshot_crossing_return_pct':sum(values)/len(values) if values else None}
                          for slip,values in costs.items()]},
            'minute_execution':{'state':'NO_FILL_PERFORMANCE','reason':'15:20 planned exit overlaps closing auction; continuous-session execution is unverified'}}
    (destination/'status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=True))
