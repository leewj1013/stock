"""Offline-only US research. Require exact reproduction before comparing execution paths."""
import csv,json,os,sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch
from revalidate_sell_timing import next_open_run, ROOT
from stock_alarm import us_strategy_account as us
from stock_alarm.us_engine import module

OUT=ROOT/'reports/research/us-execution'
SECTORS={t:g for g,ts in {'XLY':['AMZN.O','HLT','ORLY.O','SONY.K'],'XLI':['RTX','HON.O','CP','CNI','CSX.O','NSC','ITW','ADP.O'],'XLV':['TMO','DHR','ABT','ISRG.O','HCA','MRK','NTRA.O'],'XLF':['RY','MCO','MET','ICE','DB','BMO','BCS','APO'],'XLK':['TXN.O','AAPL.O','ADI.O','NXPI.O','NOW','TEL','ASX'],'XLB':['ECL'],'XLRE':['DLR','EQIX.O'],'XLE':['PSX','WMB','BKR.O','TRGP.K'],'XLU':['AEP.O'],'XLP':['UL']}.items() for t in ts}
def read(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def audit(a):
    cfg=json.loads(a.path('config.json').read_text());env={k:v for k,v in cfg['env'].items() if v}
    names={r['code']:r['name_eng'] for r in read(a.path('universe.csv'))}
    ledger=read(a.path('equity.csv'));end=ledger[-1]['date']
    calendar=read(ROOT/'reports/research/us-source-validation/nyse_sessions.csv')
    sessions=[r['session'] for r in calendar if ledger[0]['date']<=r['session']<=end]
    if sessions!=[r['date'] for r in ledger]:
        raise ValueError('NYSE calendar and confirmed ledger sessions differ; review before publishing')
    with patch.dict(os.environ,env):
        for k,v in cfg['env'].items():
            if not v:os.environ.pop(k,None)
        engine,sim,base=us.replay(a,names)
        for obtained, expected in ((us.equity_rows(base),ledger),(us.decision_rows(base),read(a.path('decisions.csv')))):
            assert len(obtained)==len(expected), ('baseline length',len(obtained),len(expected))
            for x,y in zip(obtained,expected):
                assert {k:str(v) for k,v in x.items()}==y, ('baseline mismatch',x,y)
        with patch.dict(os.environ,{'MIN_TRADING_VALUE':us.MIN_TRADING_VALUE_CENTS}):
            candidates=sim.build_candidate_cache()
            result=next_open_run('existing',module('benchmark_comparison'))(sim,'stock_alarm',candidates,20260828,liquidate_at_end=False)
            pending_sells=dict(sim.pending_sell_orders)
            sim.research_sectors=SECTORS
            try:
                limited=next_open_run('existing',module('benchmark_comparison'),sector_budget=True)(sim,'stock_alarm',candidates,20260828,liquidate_at_end=False)
                limit_result=dict(daily=us.equity_rows(limited),events=limited.events)
            except ValueError as err:
                if not str(err).startswith('unmapped sector:'):raise
                limit_result=dict(blocked=str(err))
    evidence=[];risks=[];qty=defaultdict(int)
    for day in sim.days:
        state=next(r for r in result.daily_state if r['date']==day)
        pend=set(json.loads(state['pending_json']))
        for e in result.events:
            if e['date']==day:qty[e['ticker']]+=e['shares']*(1 if e['event']=='buy' else -1)
        sector=defaultdict(float)
        for t,n in qty.items():
            if n:sector[SECTORS.get(t,'unmapped')]+=n*sim._close(t,day,0)/state['equity']*100
        risks.append(dict(day=day,sector_pct=dict(sector)))
        for e in engine.passed_evaluations(day):
            if e.pick:evidence.append(dict(day=day,ticker=e.ticker,score=e.pick.score,factors={k:v for k,v in e.values.items() if k.endswith('_score') or k in ('volume_ratio','relative_strength_pct')},state='pending_next_open' if e.ticker in pend else 'held' if qty[e.ticker] else 'not_pending_reason_unverified'))
    sells=[e for e in result.events if e['event'].startswith('sell')]
    assert all(e['decision_date']<e['date'] for e in sells)
    return dict(sector_cap_30=limit_result,end=end,baseline_return=float(ledger[-1]['return_pct']),next_open_return=float(us.equity_rows(result)[-1]['return_pct']),cost_rate=sim.cost_rate,events=result.events,daily=us.equity_rows(result),pending_sells=pending_sells,candidate_evidence=evidence,sector_risk=risks)
if __name__=='__main__':
    for a in us.ACCOUNTS:
        r=audit(a); dest=OUT/r['end'];dest.mkdir(parents=True,exist_ok=True)
        p=dest/(a.key+'.json');tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)
        print(a.key,r['baseline_return'],r['next_open_return'],len(r['candidate_evidence']),r['sector_risk'][-1],flush=True)
