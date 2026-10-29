"""Frozen pre-auction quote exit and separately labelled historical replay."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from .data_store import DB_PATH
from .profit_research import simulate, LABELS, DIP_LABELS
from .research_data_collect import KST
from .research_data_validation import check_quote
from .research_input_gate import quote_fill

PROTOCOL={'version':'day-quote-exit-v1','decision_window':['15:10','15:19'],
          'quantity':1,'max_next_quote_seconds':60,'fee_bps_each_side':15,
          'entry':'next_valid_quote_within_60_seconds_after_signal_known_and_bar_completed'}
REPORT='reports/four_remaining/status.json'
SOURCE_VERSION='kr-opening-range-v2'


def pre_auction_exit(quotes,calendar):
    """Decide on the first valid window quote; do not retry a missed execution."""
    ordered=sorted(quotes,key=lambda r:r['observed_at'])
    for index,row in enumerate(ordered):
        stamp=datetime.fromisoformat(row['observed_at']).astimezone(KST)
        if not PROTOCOL['decision_window'][0]<=stamp.strftime('%H:%M')<PROTOCOL['decision_window'][1]:continue
        if check_quote(row)!='VALID_INTEGRATED_SNAPSHOT':continue
        for following in ordered[index+1:]:
            if following['ticker']!=row['ticker']:continue
            money=quote_fill(following,'sell',PROTOCOL['quantity'],stamp,calendar,fee_bps=PROTOCOL['fee_bps_each_side'])
            if money is not None:return {'decision_at':row['observed_at'],'fill_at':following['observed_at'],'net_proceeds':money}
        return None
    return None


def quote_cases(signals,quotes,calendar,activated_at,as_of=None):
    as_of=as_of or datetime.now(KST)
    results=[];activation=datetime.fromisoformat(activated_at)
    for row in signals:
        known=datetime.fromisoformat(row['checked_at'])
        if known<activation or known>as_of:continue
        signal=json.loads(row['signal_json'])
        decision=max(known,datetime.fromisoformat(signal['entry_earliest_at']))
        tape=sorted((q for q in quotes if q['ticker']==row['ticker'] and q['observed_at'][:10]==row['signal_day']
                     and datetime.fromisoformat(q['observed_at'])<=as_of),key=lambda q:q['observed_at'])
        entry=None
        for q in tape:
            cost=quote_fill(q,'buy',PROTOCOL['quantity'],decision,calendar,fee_bps=PROTOCOL['fee_bps_each_side'])
            if cost is not None:entry=(q,cost);break
        result={'ticker':row['ticker'],'signal_day':row['signal_day'],'decision_at':decision.isoformat(),
                'state':'ENTRY_DATA_MISSING' if as_of>decision+timedelta(seconds=60) else 'WAITING_ENTRY_QUOTE'}
        if entry:
            exit_deadline=datetime.fromisoformat(row['signal_day']+'T15:20:00+09:00')
            exit_start=datetime.fromisoformat(row['signal_day']+'T15:10:00+09:00')
            result.update(state='EXIT_DATA_MISSING' if as_of>=exit_deadline else 'WAITING_EXIT_WINDOW' if as_of<exit_start else 'WAITING_EXIT_QUOTE',
                          entry_at=entry[0]['observed_at'])
            exit_result=pre_auction_exit([q for q in tape if q['observed_at']>entry[0]['observed_at']],calendar)
            if exit_result:
                result.update(state='SCENARIO_COMPLETE',exit=exit_result,net_return_pct=(exit_result['net_proceeds']/entry[1]-1)*100)
        results.append(result)
    return results


def record_quote_cases(db,signals,quotes,calendar,registry,as_of=None):
    """Freeze original signal inputs and terminal outcomes under the registered version."""
    as_of=as_of or datetime.now(KST);version=registry['rules']['version']
    db.execute('BEGIN IMMEDIATE')
    db.execute('''CREATE TABLE IF NOT EXISTS quote_exit_cases(
        version TEXT,ticker TEXT,signal_day TEXT,signal TEXT NOT NULL,outcome TEXT,
        evidence TEXT,updated_at TEXT,PRIMARY KEY(version,ticker,signal_day))''')
    for signal in signals:
        known=datetime.fromisoformat(signal['checked_at'])
        if datetime.fromisoformat(registry['activated_at'])<=known<=as_of:
            db.execute('INSERT OR IGNORE INTO quote_exit_cases(version,ticker,signal_day,signal) VALUES(?,?,?,?)',
                       (version,signal['ticker'],signal['signal_day'],json.dumps(signal,sort_keys=True)))
    results=[]
    for row in db.execute('SELECT ticker,signal_day,signal,outcome,updated_at FROM quote_exit_cases WHERE version=? ORDER BY signal_day,ticker',(version,)).fetchall():
        ticker,day,raw,outcome,updated_at=row
        if updated_at and datetime.fromisoformat(updated_at)>as_of:raise ValueError('Research clock moved backwards')
        result=json.loads(outcome) if outcome else None
        if result and result['state'] in ('SCENARIO_COMPLETE','ENTRY_DATA_MISSING','EXIT_DATA_MISSING'):
            results.append(result);continue
        frozen=json.loads(raw)
        calculated=quote_cases([frozen],quotes,calendar,registry['activated_at'],as_of)
        if not calculated:continue
        result=calculated[0]
        until=(result.get('exit') or {}).get('fill_at') or as_of.isoformat()
        source={'signal':frozen,'quotes':[q for q in quotes if q['ticker']==ticker and q['observed_at'][:10]==day
                and datetime.fromisoformat(q['observed_at'])<=datetime.fromisoformat(until)],'calendar':calendar}
        raw_evidence=json.dumps(source,sort_keys=True,ensure_ascii=False)
        result['evidence_sha256']=hashlib.sha256(raw_evidence.encode()).hexdigest()
        db.execute('UPDATE quote_exit_cases SET outcome=?,evidence=?,updated_at=? WHERE version=? AND ticker=? AND signal_day=?',
                   (json.dumps(result,ensure_ascii=False),raw_evidence,as_of.isoformat(),version,ticker,day))
        results.append(result)
    db.commit()
    return results


def replay_closed(buys,checks,sales):
    """Historical exploration only; complete price paths are not reconstructed."""
    results=[];excluded=Counter()
    for trade in buys:
        later=[r['created_at'] for r in buys if r['ticker']==trade['ticker'] and r['created_at']>trade['created_at']]
        end=min(later) if later else '9999'
        sold=[r for r in sales if r['ticker']==trade['ticker'] and trade['created_at']<=r['created_at']<end]
        if sum(int(r['quantity']) for r in sold)!=int(trade['quantity']):excluded['OPEN_OR_OVERLAPPING']+=1;continue
        final=max(r['created_at'] for r in sold)
        tape=[r for r in checks if r['ticker']==trade['ticker'] and trade['created_at']<r['checked_at']<final]
        if not tape:excluded['NO_INTERMEDIATE_OBSERVATIONS']+=1;continue
        try:
            normal=simulate(trade,tape,sold);dips=simulate(trade,tape,sold,dips=True)
            if not normal['complete'] or not dips['complete']:raise ValueError('Unclosed replay')
            base=normal['arms']['baseline']['net_return_pct']
            cash_base=sum(float(s['price'])*int(s['quantity']) for s in sold)*.9985
            expected=(cash_base/(float(trade['price'])*int(trade['quantity'])*1.0015)-1)*100
            if abs(base-expected)>1e-8:raise ValueError('Baseline does not reproduce sales')
            arms={**{k:normal['arms'][k] for k in LABELS},**{k:dips['arms'][k] for k in DIP_LABELS}}
            results.append({'trade_id':trade['trade_id'],'ticker':trade['ticker'],'baseline_pct':base,
                            'first_take_profit':bool(normal['first_take_profit_at']),
                            'extra_exits':{k:any(e['reason']==k for e in a['exits']) for k,a in arms.items()},
                            'differences_pctp':{k:a['net_return_pct']-base for k,a in arms.items()}})
        except (ValueError,KeyError,TypeError):excluded['INVALID_PATH']+=1
    rows=[]
    for key,label in {**LABELS,**DIP_LABELS}.items():
        differences=[r['differences_pctp'][key] for r in results]
        rows.append({'key':key,'label':label,'paired_trades':len(differences),
                     'first_take_profit_eligible':sum(r['first_take_profit'] for r in results) if key in ('principal','trail') else None,
                     'extra_exit_cases':sum(r['extra_exits'][key] for r in results),
                     'mean_difference_pctp':sum(differences)/len(differences) if differences else None,
                     'improved':sum(v>1e-9 for v in differences),'worsened':sum(v< -1e-9 for v in differences)})
    return {'scope':'HISTORICAL_EXPLORATORY_NOT_FORWARD_RESULTS','included':len(results),
            'excluded':dict(excluded),'comparisons':rows,'cases':results,
            'source_sha256':hashlib.sha256(json.dumps([buys,checks,sales],sort_keys=True,ensure_ascii=False).encode()).hexdigest()}


def run(source=DB_PATH,report=REPORT):
    from .research_extensions import connect, PATH
    from .day_trade_research import VERSION as active_signal_version
    if active_signal_version!=SOURCE_VERSION:raise ValueError('Signal rule changed; register a new quote-exit version')
    with closing(connect(PATH)) as db:
        saved=db.execute("SELECT value FROM metadata WHERE key='day_quote_exit_v1'").fetchone()
        registry=json.loads(saved[0]) if saved else {'activated_at':datetime.now(KST).isoformat(),'rules':PROTOCOL}
        if registry['rules']!=PROTOCOL:raise ValueError('Quote exit protocol changed')
        db.execute("INSERT OR IGNORE INTO metadata VALUES('day_quote_exit_v1',?)",(json.dumps(registry,sort_keys=True),));db.commit()
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        buys=[dict(r) for r in db.execute('SELECT * FROM virtual_trades ORDER BY created_at,trade_id')]
        sales=[dict(r) for r in db.execute('SELECT * FROM virtual_sales ORDER BY created_at,sale_id')]
        checks=[dict(r) for r in db.execute('SELECT DISTINCT ticker,checked_at,close FROM position_checks WHERE close>0 AND return_pct IS NOT NULL ORDER BY checked_at')]
    cohort=[]
    for file,table in [('data/profit_research.db','cases'),('data/profit_research_dips_v1.db','cases'),(PATH,'expiry_cases')]:
        with closing(sqlite3.connect(Path(file).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            start=db.execute("SELECT value FROM metadata WHERE key='activated_at'").fetchone()[0]
            cohort.append({'database':file,'start':start,'new_buys':sum(r['created_at']>=start for r in buys),
                           'registered':db.execute('SELECT count(*) FROM '+table).fetchone()[0]})
    with closing(sqlite3.connect(Path('data/research_data_v1.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        quotes=[dict(r) for r in db.execute('SELECT * FROM quotes ORDER BY observed_at')]
        calendar={}
        for row in db.execute("SELECT details FROM collection_status WHERE kind='calendar' AND state='COLLECTED' ORDER BY checked_at"):
            for value in json.loads(row['details']).values():
                if isinstance(value,dict) and value.get('date'):calendar[value['date']]=value
    with closing(sqlite3.connect(Path('data/domestic_horizon_research.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        signals=[dict(r) for r in db.execute("SELECT * FROM day_signals WHERE state='SIGNAL_RECORDED' AND rule_version=?",(SOURCE_VERSION,))]
    with closing(connect(PATH)) as db:
        cases=record_quote_cases(db,signals,quotes,calendar,registry)
    counts=Counter(r['state'] for r in cases)
    progress={'registered':len(cases),'complete':counts['SCENARIO_COMPLETE'],
              'waiting':sum(v for k,v in counts.items() if k.startswith('WAITING_')),
              'excluded_missing_data':counts['ENTRY_DATA_MISSING']+counts['EXIT_DATA_MISSING']}
    result={'checked_at':datetime.now(KST).isoformat(),'prospective_cohorts':cohort,'quote_exit_protocol':registry,
            'signal_version':SOURCE_VERSION,
            'quote_exit_state':'SCENARIO_RESULTS_AVAILABLE' if any(r['state']=='SCENARIO_COMPLETE' for r in cases) else 'REGISTERED_AWAITING_ELIGIBLE_SIGNAL_AND_PAIRED_QUOTES',
            'quote_exit_cases':cases,'quote_exit_counts':dict(counts),'quote_exit_progress':progress,
            'historical_replay':replay_closed(buys,checks,sales)}
    target=Path(report);target.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,delete=False,suffix='.tmp') as file:
        json.dump(result,file,ensure_ascii=False,indent=2);temporary=Path(file.name)
    try:temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    return result


if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=True))
