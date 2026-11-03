"""Read-only recommendation timing experiment; no orders or broker calls."""
import json,sqlite3,math,sys
from pathlib import Path
from datetime import datetime,timedelta,time,timezone
from collections import Counter,defaultdict
from statistics import mean
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/research/kr-ma-entry'
KST=timezone(timedelta(hours=9))
FORWARD='2026-11-03'
def cross(prices,i):
    # Compare 5*4 versus 20 to avoid division.
    return i>=20 and 4*sum(prices[i-5:i])<=sum(prices[i-20:i]) and 4*sum(prices[i-4:i+1])>sum(prices[i-19:i+1])
def net(entry,exit_price):
    qty=int(1_000_000//(entry*1.0015))
    return qty*(exit_price*.9985-entry*1.0015)/1_000_000*100
def evaluate(stamp,bars):
    start=stamp.replace(hour=9,minute=0,second=0,microsecond=0)
    finish=start+timedelta(hours=6,minutes=15)
    first=int((stamp-start).total_seconds()//60)+1
    if not 0<=first<375:return dict(state='outside_window')
    times=[start+timedelta(minutes=i) for i in range(376)]
    if any(t not in bars for t in times):return dict(state='missing_minutes')
    seq=[bars[t] for t in times]
    if any(not all(math.isfinite(b[k]) and b[k]>0 for k in ('open','high','low','close')) or b['low']>min(b['open'],b['close']) or b['high']<max(b['open'],b['close']) for b in seq):return dict(state='invalid_price')
    prices=[b['close'] for b in seq]
    entry=next((i+1 for i in range(max(20,first),374) if cross(prices,i)),None)
    if any(seq[i]['volume']<=0 for i in [first,375]+([entry] if entry is not None else [])):return dict(state='execution_unverified')
    baseline=net(seq[first]['open'],seq[375]['open']); wait=net(seq[entry]['open'],seq[375]['open']) if entry is not None else 0.
    fixed=first+10
    if fixed>=375:
        fixed_state,fixed_pct='cash_cutoff',0.
    elif seq[fixed]['volume']<=0:
        fixed_state,fixed_pct='execution_unverified',None
    else:
        fixed_state,fixed_pct='entered',net(seq[fixed]['open'],seq[375]['open'])
    return dict(state='matched',fixed_state=fixed_state,fixed_at=times[fixed].isoformat() if fixed_state=='entered' else None,fixed_pct=fixed_pct,cross_minus_fixed_pp=wait-fixed_pct if fixed_pct is not None else None,baseline_at=times[first].isoformat(),cross_entry_at=times[entry].isoformat() if entry is not None else None,exit_at=finish.isoformat(),immediate_pct=baseline,wait_pct=wait,diff_pp=wait-baseline)
def connect(name):
    c=sqlite3.connect((ROOT/'data'/name).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('BEGIN');return c
def forward(day,ticker,stamp,now,collect):
    from stock_alarm.market_sessions import day_info
    path=OUT/'forward'/day/(ticker+'.json')
    if path.is_file():
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved['stamp']!=stamp.isoformat():return dict(state='recommendation_revised')
        return saved['result']
    if not collect or day!=now.date().isoformat():return dict(state='not_collected')
    session=day_info(now)
    if not session or not session['open'] or session['changed']:return dict(state='session_unverified_or_changed')
    if now<session['end']:return dict(state='in_session')
    from stock_alarm.minute_candle_collect import fetch_day
    from stock_alarm.toss_client import shared_client
    diagnostics={}
    try:
        rows=fetch_day(shared_client(),ticker,stamp.date(),now,diagnostics)
    except Exception as error:
        return dict(state='collection_failed',error=type(error).__name__)
    if diagnostics.get('duplicate_source'):return dict(state='duplicate_minutes')
    bars={datetime.fromisoformat(r[1]):dict(zip(('open','high','low','close','volume'),r[2:7])) for r in rows}
    result=evaluate(stamp,bars)
    if result['state']=='matched':
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as f:
            json.dump(dict(stamp=stamp.isoformat(),observed=now.isoformat(),result=result,bars=rows),f,ensure_ascii=False)
    return result

def run(collect=False):
    sys.path.insert(0,str(ROOT))
    if collect:
        from stock_alarm.app import load_env
        load_env()
    now=datetime.now(KST);source=connect('stock_alarm.db');minutes=connect('domestic_horizon_research.db')
    recs=source.execute("""select r.market_date day,r.finished_at,c.ticker,r.run_id from strategy_runs r join candidate_snapshots c using(run_id) where r.run_type='recommendation' and r.status='completed' and c.selected=1 and r.market_date>='2026-10-26' and r.market_date<=? order by r.finished_at,r.run_id,c.ticker""",(now.date().isoformat(),)).fetchall()
    seen=set();results=[]
    for rec in recs:
        d,t=rec['day'],rec['ticker']
        if (d,t) in seen:continue
        seen.add((d,t));stamp=datetime.fromisoformat(rec['finished_at']);stamp=stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp.astimezone(KST)
        if d>=FORWARD:
            res=forward(d,t,stamp,now,collect) if stamp.date().isoformat()==d else dict(state='timestamp_mismatch')
            results.append({**dict(rec),'cohort':'forward',**res})
            continue
        q=minutes.execute("select status,duplicate_source,expected from minute_quality where ticker=? and signal_day=?",(t,d)).fetchone()
        error=minutes.execute("select error from observations where ticker=? and signal_day=? and kind='day'",(t,d)).fetchone()
        bars={datetime.fromisoformat(b['bar_at']):b for b in minutes.execute("select * from minute_bars where ticker=? and bar_at>=? and bar_at<? and source='toss_unadjusted_start_v2'",(t,d+'T00',d+'T24'))}
        if stamp.date().isoformat()!=d:res=dict(state='timestamp_mismatch')
        elif not bars:res=dict(state='no_minutes')
        elif not q or q['status']!='COMPLETE' or q['expected']!=390 or q['duplicate_source'] or (error and error[0]):res=dict(state='quality_unverified')
        else:res=evaluate(stamp,bars)
        results.append({**dict(rec),'cohort':'forward' if d>=FORWARD else 'pilot',**res})
    source.close();minutes.close()
    summary={}
    for cohort in ('pilot','forward'):
        group=[r for r in results if r['cohort']==cohort];valid=[r for r in group if r['state']=='matched'];days=defaultdict(list)
        for r in valid:days[r['day']].append(r)
        summary[cohort]=dict(all=len(group),states=dict(Counter(r['state'] for r in group)),days=len(days),crosses=sum(bool(r['cross_entry_at']) for r in valid),date_equal_weight={k:mean(mean(r[k] for r in v) for v in days.values()) for k in ('immediate_pct','wait_pct','diff_pp')} if days else None)
        triples=defaultdict(list)
        for r in valid:
            if r.get('fixed_pct') is not None:triples[r['day']].append(r)
        summary[cohort]['three_way']=dict(n=sum(map(len,triples.values())),days=len(triples),
            fixed_states=dict(Counter(r.get('fixed_state','not_recorded') for r in valid)),
            date_equal_weight={k:mean(mean(r[k] for r in day) for day in triples.values()) for k in
                               ('immediate_pct','fixed_pct','wait_pct','cross_minus_fixed_pp')} if triples else None)
    dest=OUT/now.strftime('%Y%m%dT%H%M%S');dest.mkdir(parents=True)
    (dest/'results.json').write_text(json.dumps(dict(raw_selected_rows=len(recs),summary=summary,records=results),ensure_ascii=False,indent=2),encoding='utf-8');print(summary)
if __name__=='__main__':run('--collect' in sys.argv)
