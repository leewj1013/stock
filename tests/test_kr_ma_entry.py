from scripts.kr_ma_entry import evaluate,KST,cross,net
from datetime import datetime,timedelta
def test_cross_and_execution():
    d=datetime(2026,11,2,9,tzinfo=KST)
    bars={d+timedelta(minutes=i):dict(open=100,high=102,low=99,close=101 if i==30 else 100,volume=100) for i in range(376)}
    r=evaluate(d+timedelta(minutes=25,seconds=3),bars)
    assert r['baseline_at']==(d+timedelta(minutes=26)).isoformat()
    assert r['cross_entry_at']==(d+timedelta(minutes=31)).isoformat()
    bars[d+timedelta(minutes=30)]['close']=100
    r=evaluate(d+timedelta(minutes=25),bars)
    assert r['cross_entry_at'] is None and r['wait_pct']==0 and r['immediate_pct']<0
    del bars[d+timedelta(minutes=31)]
    assert evaluate(d+timedelta(minutes=25),bars)['state']=='missing_minutes'

def test_fixed_delay_and_cash_cutoff():
    start=datetime(2026,11,2,9,tzinfo=KST)
    bars={start+timedelta(minutes=i):dict(open=100,close=100,high=125,low=99,volume=1) for i in range(376)}
    fixed=start+timedelta(minutes=36)
    bars[fixed]['open']=120
    r=evaluate(start+timedelta(minutes=25,seconds=3),bars)
    assert r['fixed_at']==fixed.isoformat() and r['fixed_pct']==net(120,100)
    assert r['cross_minus_fixed_pp']==-r['fixed_pct']
    bars[fixed]['volume']=0
    assert evaluate(start+timedelta(minutes=25),bars)['fixed_pct'] is None
    r=evaluate(start+timedelta(minutes=364),bars)
    assert r['fixed_state']=='cash_cutoff' and r['fixed_pct']==0 and r['fixed_at'] is None
