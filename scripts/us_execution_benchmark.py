"""Compare research fills to identical sector ETF orders; dividends accrue on ex-date."""
import csv,json,sys
from collections import defaultdict
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import requests
from us_execution_study import OUT,SECTORS
def quote(symbol,start,end):
    p=OUT/end/'quotes'/f'{symbol}.json';p.parent.mkdir(exist_ok=True)
    ts=lambda day:int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    if not p.exists():
        r=requests.get('https://query1.finance.yahoo.com/v8/finance/chart/'+symbol,params={'period1':ts(start),'period2':ts(end)+86400,'interval':'1d','events':'div,splits'},headers={'User-Agent':'Mozilla/5.0'},timeout=15);r.raise_for_status();j=r.json()['chart']['result'][0]
        assert j['meta']['symbol']==symbol and j['meta']['currency']=='USD'
        p.write_text(json.dumps(j),encoding='utf-8')
    j=json.loads(p.read_text(encoding='utf-8'));events=j.get('events',{})
    if events.get('splits'):raise ValueError(f'{symbol}: split needs reconciliation')
    day=lambda sec:datetime.fromtimestamp(sec,timezone.utc).date().isoformat()
    return {day(t):(j['indicators']['quote'][0]['open'][i],j['indicators']['quote'][0]['close'][i]) for i,t in enumerate(j['timestamp'])},{day(e['date']):e['amount'] for e in events.get('dividends',{}).values()}
def compare(r):
    days=[x['date'] for x in r['daily']]; events=r['events']; symbols={e['ticker']:e['ticker'].split('.')[0] for e in events}
    if set(symbols)-SECTORS.keys():raise ValueError(f'Freeze missing sector mapping: {set(symbols)-SECTORS.keys()}')
    feed={s:quote(s,days[0],days[-1]) for s in sorted(set(symbols.values())|{SECTORS[t] for t in symbols})}
    cash=[100000.,100000.];qty=defaultdict(lambda:[0.,0.]);income=[0.,0.];series=[];fee=.0015
    for d in days:
        for t,q in qty.items():
            for i,s in enumerate((symbols[t],SECTORS[t])):income[i]+=q[i]*feed[s][1].get(d,0)
        for e in [e for e in events if e['date']==d]:
            t=e['ticker'];buy=e['event']=='buy';n=e['shares'];ep=feed[SECTORS[t]][0][d][0];stock=e['price']/100
            assert abs(feed[symbols[t]][0][d][0]/stock-1)<.005, f'{t}: open price mismatch'
            units=n*stock/ep if buy else qty[t][1]*n/qty[t][0]
            for i,(units_i,px) in enumerate(((n,stock),(units,ep))):
                qty[t][i]+=units_i*(1 if buy else -1);cash[i]+=units_i*px*(-(1+fee) if buy else 1-fee)
        assert min(cash)>=0,'fee scenario overdrawn; resize before reporting'
        v=[cash[i]+income[i]+sum(q[i]*feed[(symbols[t],SECTORS[t])[i]][0][d][1] for t,q in qty.items() if q[i]) for i in (0,1)]
        series.append({'date':d,'stock':v[0],'etf':v[1]})
    return dict(daily=series,dividends_usd=income,net_pct=[(v/100000-1)*100 for v in (series[-1]['stock'],series[-1]['etf'])])
if __name__=='__main__':
    directory=OUT/sys.argv[1]
    for key in ('100','300'):
        res=compare(json.loads((directory/(key+'.json')).read_text(encoding='utf-8')));(directory/(key+'-paired.json')).write_text(json.dumps(res,indent=2),encoding='utf-8');print(key,res['net_pct'],res['dividends_usd'])
