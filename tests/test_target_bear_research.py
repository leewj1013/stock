from datetime import date, timedelta

import pytest

from stock_alarm import target_bear_research as research
from stock_alarm.sell_check import take_profit_policy_snapshot


def test_previous_session_label_never_uses_buy_day():
    days=[(date(2026,1,1)+timedelta(days=i)).strftime('%Y%m%d') for i in range(62)]
    points=[(d,100 if i<59 else 50 if i==59 else 200) for i,d in enumerate(days)]
    labels=research.bear_labels(points)
    bought=(date(2026,1,1)+timedelta(days=60)).isoformat()+'T10:00:00'
    assert research.previous_label(bought,points,labels)['bear']
    assert not labels[days[60]]['bear']
    assert research.previous_label('2027-01-01T10:00:00',points,labels) is None
    with pytest.raises(ValueError):research.bear_labels(points[::-1])


def test_half_size_uses_integer_shares_and_keeps_idle_cash():
    trade={'quantity':3,'price':100}
    sales=[{'created_at':'a','sale_id':1,'quantity':1,'price':120},
           {'created_at':'b','sale_id':2,'quantity':2,'price':90}]
    expected=(90*.9985-100*1.0015)/(300*1.0015)*100
    assert research.half_contribution(trade,sales)==pytest.approx(expected)
    assert research.half_contribution({'quantity':1,'price':100},[{'created_at':'a','sale_id':1,'quantity':1,'price':90}])==0
    with pytest.raises(ValueError):research.half_contribution(trade,sales[:1])


def test_target_groups_require_observations_after_tp1():
    path={'trade':{'trade_id':1},'sales':[{'created_at':'b','stage':'take_profit_1'},
         {'created_at':'d','stage':'','reason':'손절'}], 'tape':[('a',5),('b',11),('c',9),('d',-2)]}
    rows={r['key']:r for r in research.target_groups([path])}
    assert rows['rose_below_10']['count']==0
    assert rows['tp1_then_le10']['pct']==100
    assert rows['tp1_below_20']['count']==1
    assert rows['tp1_no_tp2']['final_sales'][0]['reason']=='손절'


def test_dashboard_target_bear_results_and_missing_report(tmp_path):
    import json
    from stock_alarm.profit_research import target_bear_dashboard_data
    path=tmp_path/'results.json'
    assert target_bear_dashboard_data(path)=={'available':False}
    path.write_text(json.dumps({'generated_at':'now','bear_unknown':0,
        'target_groups':[{'label':'목표','count':1,'denominator':2,'pct':50,
                          'final_sales':[{'stage':'','reason':'<매도>','count':1}]}],
        'bear_comparisons':[{'label':'절반','n':2,'mean_pct':1,'mean_difference_pctp':.5,
                             'worst_pct':-1,'improved':1,'neutral':1,'worsened':0}]}),encoding='utf-8')
    data=target_bear_dashboard_data(path)
    assert data['targets'][0]==['목표','1/2','50.0%']
    assert data['bears'][0][3]=='+0.500%p'


def test_policy_snapshot_resolves_environment_and_profile_override(monkeypatch):
    monkeypatch.setenv('TAKE_PROFIT_1_PCT','11')
    assert take_profit_policy_snapshot()['take_profit_1_pct']==11
    value=take_profit_policy_snapshot({'take_profit_1_pct':7,'market_regime':'bull','disable_take_profit_in_regimes':['bull']})
    assert value['take_profit_1_pct']==7 and not value['take_profit_allowed']


def test_position_snapshot_includes_evaluated_targets(monkeypatch):
    from stock_alarm import sell_check
    position={'ticker':'A','entry_price':'100','entry_date':'2026-10-28'}
    rows=[[(date(2026,10,1)+timedelta(days=i)).strftime('%Y%m%d'),100,100,100,100,1] for i in range(25)]
    monkeypatch.setattr(sell_check,'naver_rows',lambda *a:rows)
    monkeypatch.setattr('stock_alarm.toss_client.blocking_warnings_for',lambda *a:[])
    _,snapshot=sell_check.position_snapshot(position,date(2026,10,29),remaining_quantity=10,
        sell_policy={'take_profit_1_pct':7,'take_profit_2_pct':14})
    assert snapshot['take_profit_1_pct']==7 and snapshot['take_profit_2_pct']==14
    assert snapshot['take_profit_allowed'] is True


def test_target_snapshot_is_persisted_and_old_rows_stay_unknown(tmp_path):
    from stock_alarm.data_store import connect, start_run, write_position_checks
    path=str(tmp_path/'source.db')
    run_id=start_run('test','2026-10-29',path=path)
    row={'position_id':'p','checked_at':'2026-10-29T10:00:00','ticker':'A','decision':'HOLD','reasons':''}
    write_position_checks(run_id,[row],path=path)
    with connect(path) as db:
        assert db.execute('SELECT take_profit_1_pct FROM position_checks').fetchone()[0] is None
    write_position_checks(run_id,[{**row,**take_profit_policy_snapshot({'take_profit_1_pct':7})}],path=path)
    with connect(path) as db:
        assert tuple(db.execute('SELECT take_profit_1_pct,take_profit_allowed FROM position_checks').fetchone())==(7,1)
