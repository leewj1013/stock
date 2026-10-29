"""Retrospective target diagnostics and isolated bear-entry comparisons; no orders."""
from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_left
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean

from .profit_research import simulate
from .research_followup import replay_closed

REPORT = 'reports/target_bear_research/results.json'
RULES = {'version':'target-bear-exploration-v1', 'benchmark_targets_pct':[10,20],
         'bear':'previous_session_close_below_ma60_and_ma20_below_ma60',
         'fee_bps_each_side':15, 'half_quantity':'floor_original_quantity_divided_by_2',
         'cash':'idle_no_interest_no_reinvestment', 'early':'existing_5pct_half_exit_next_saved_price'}


def bear_labels(points):
    days=[str(d) for d,_ in points]
    if days != sorted(set(days)) or any(not math.isfinite(float(v)) or v<=0 for _,v in points):
        raise ValueError('Invalid saved index series')
    result={}
    for i,(day,close) in enumerate(points):
        if i<59:
            continue
        ma60=mean(v for _,v in points[i-59:i+1]);ma20=mean(v for _,v in points[i-19:i+1])
        result[day]={'bear':close<ma60 and ma20<ma60,'close':close,'ma20':ma20,'ma60':ma60}
    return result


def previous_label(bought_at, points, labels):
    days=[d for d,_ in points];day=bought_at[:10].replace('-','')
    i=bisect_left(days,day)
    if i==0 or i==len(days) or days[i]!=day or days[i-1] not in labels:
        return None
    return {'day':days[i-1],**labels[days[i-1]]}


def half_contribution(trade, sales):
    """Keep integer shares and distribute actual partial exits by cumulative floor."""
    quantity=int(trade['quantity']);half=quantity//2
    sold=allocated=0;cash=0.0
    for row in sorted(sales,key=lambda r:(r['created_at'],r['sale_id'])):
        sold+=int(row['quantity'])
        target=sold*half//quantity
        cash+=(target-allocated)*float(row['price'])*.9985
        allocated=target
    if sold!=quantity or allocated!=half:
        raise ValueError('Incomplete or oversold half-size path')
    capital=float(trade['price'])*quantity*1.0015
    return (cash-half*float(trade['price'])*1.0015)/capital*100


def target_groups(paths):
    groups={key:[] for key in ('rose_below_10','tp1_then_le10','tp1_below_20','tp1_no_tp2')}
    for path in paths:
        tape=path['tape'];sales=path['sales']
        first=min((s['created_at'] for s in sales if s.get('stage')=='take_profit_1'),default=None)
        if any(v>1e-9 for _,v in tape) and not any(v>=10-1e-9 for _,v in tape):
            groups['rose_below_10'].append(path)
        if first:
            after=[v for at,v in tape if at>first]
            if any(v<=10+1e-9 for v in after):groups['tp1_then_le10'].append(path)
            if after and not any(v>=20-1e-9 for v in after):groups['tp1_below_20'].append(path)
            if not any(s.get('stage')=='take_profit_2' for s in sales):groups['tp1_no_tp2'].append(path)
    denominator={'rose_below_10':len(paths),**{k:sum(any(s.get('stage')=='take_profit_1' for s in p['sales']) for p in paths) for k in groups if k!='rose_below_10'}}
    labels={'rose_below_10':'양수 관측은 있으나 +10% 도달 미관측',
            'tp1_then_le10':'실제 1차 익절 후 +10% 이하 재관측',
            'tp1_below_20':'실제 1차 익절 후 +20% 도달 미관측',
            'tp1_no_tp2':'실제 1차 익절 후 2차 익절 체결 없음'}
    result=[]
    for key,items in groups.items():
        handling=Counter((p['sales'][-1].get('stage') or '없음',p['sales'][-1].get('reason') or '미기재') for p in items)
        n=denominator[key]
        result.append({'key':key,'label':labels[key],'count':len(items),'denominator':n,
                       'pct':100*len(items)/n if n else None,'trade_ids':[p['trade']['trade_id'] for p in items],
                       'final_sales':[{'stage':k[0],'reason':k[1],'count':v} for k,v in handling.items()]})
    return result


def run(snapshot='reports/current_data_experiment/input_snapshot.json', report=REPORT):
    from .dashboard_v2 import _index_cache
    source=json.loads(Path(snapshot).read_text(encoding='utf-8'))
    buys,checks,sales=(source[k] for k in ('buys','checks','sales'))
    replay=replay_closed(buys,checks,sales)
    paths=[]
    for case in replay['cases']:
        trade=next(b for b in buys if b['trade_id']==case['trade_id'])
        end=min((b['created_at'] for b in buys if b['ticker']==trade['ticker'] and b['created_at']>trade['created_at']),default='9999')
        sold=sorted((s for s in sales if s['ticker']==trade['ticker'] and trade['created_at']<=s['created_at']<end),key=lambda r:(r['created_at'],r['sale_id']))
        final=sold[-1]['created_at']
        observed=[r for r in checks if r['ticker']==trade['ticker'] and trade['created_at']<r['checked_at']<final]
        tape=sorted([(r['checked_at'],(float(r['close'])/trade['price']-1)*100) for r in observed]+[(s['created_at'],(float(s['price'])/trade['price']-1)*100) for s in sold])
        paths.append({'trade':trade,'sales':sold,'checks':observed,'tape':tape,'baseline_pct':case['baseline_pct']})
    points=_index_cache('KOSPI');labels=bear_labels(points)
    selected=[];unknown=0
    for path in paths:
        label=previous_label(path['trade']['created_at'],points,labels)
        if label is None:unknown+=1;continue
        if not label['bear']:continue
        normal=simulate(path['trade'],path['checks'],path['sales'])
        selected.append({'trade_id':path['trade']['trade_id'],'ticker':path['trade']['ticker'],
                         'bought_at':path['trade']['created_at'],'label':label,
                         'baseline':path['baseline_pct'],'half':half_contribution(path['trade'],path['sales']),
                         'skip':0.0,'early':normal['arms']['early']['net_return_pct']})
    comparison=[]
    for key,label in [('baseline','기존 매수·매도'),('half','신규 매수 수량 절반'),('skip','신규 매수 보류·현금 유지'),('early','+5% 절반 익절 추가')]:
        values=[r[key] for r in selected];diffs=[r[key]-r['baseline'] for r in selected]
        comparison.append({'key':key,'label':label,'n':len(values),'mean_pct':mean(values) if values else None,
                           'mean_difference_pctp':mean(diffs) if diffs else None,
                           'worst_pct':min(values) if values else None,
                           'improved':sum(v>1e-9 for v in diffs),'worsened':sum(v< -1e-9 for v in diffs),
                           'neutral':sum(abs(v)<=1e-9 for v in diffs)})
    # Persist a price-series diagnostic; a completed recovery window is not a trading result.
    days=[d for d,_ in points];episodes=[];start=None
    for day in days:
        if day not in labels:continue
        if labels[day]['bear'] and start is None:start=day
        if not labels[day]['bear'] and start:
            i=days.index(day)
            episodes.append({'start':start,'recovery':day,'recovery_followup_sessions':min(20,len(days)-i-1),
                             'followup_complete':len(days)-i-1>=20});start=None
    if start:episodes.append({'start':start,'recovery':None,'recovery_followup_sessions':0,'followup_complete':False})
    evidence=Counter()
    for path in paths:
        for s in path['sales']:
            for stage,value in re.findall(r'([12])차 익절 목표 \+([\d.]+)%',s.get('reason') or ''):
                evidence[(stage,value)]+=1
    result={'generated_at':datetime.now().astimezone().isoformat(),'rules':RULES,'scope':'HISTORICAL_EXPLORATORY_NOT_FORWARD_RESULTS',
            'included':len(paths),'excluded':replay['excluded'],'source_sha256':replay['source_sha256'],
            'target_groups':target_groups(paths),'target_evidence':[{'stage':k[0],'pct':float(k[1]),'sale_events':v} for k,v in evidence.items()],
            'target_limit':'10/20 are fixed diagnostic benchmarks, not a backfill of unknown historical settings',
            'bear_comparisons':comparison,'bear_cases':selected,'bear_unknown':unknown,'bear_nonbear':len(paths)-len(selected)-unknown,
            'bear_episodes':episodes,'index_first':days[0] if days else None,'index_last':days[-1] if days else None,
            'account_mdd':None,'account_mdd_reason':'No alternate-account reallocation and daily equity path simulated'}
    assert sum(r['improved']+r['neutral']+r['worsened']!=r['n'] for r in comparison)==0
    target=Path(report);target.parent.mkdir(parents=True,exist_ok=True)
    (target.parent/'index_snapshot.json').write_text(json.dumps(points),encoding='utf-8')
    result['index_sha256']=hashlib.sha256(json.dumps(points).encode()).hexdigest()
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 목표가·하락장 추가 연구',f"작성 {result['generated_at']}",'',f"과거 종료·관측 거래 {len(paths)}건. 입력은 기존 동결 스냅샷을 재사용한다.",'','## 목표 도달 진단','', '모든 과거 목표 설정은 확인되지 않았다. +10%·+20%는 고정 진단 기준이며 실제 당시 목표값으로 소급 확정하지 않는다. 관측 없음은 장중 미도달 증명이 아니다. 실제 익절 단계는 체결 기록을 사용한다.','', '| 조건 | 해당/분모 | 비율 |','|---|---:|---:|']
    for r in result['target_groups']:
        lines.append(f"| {r['label']} | {r['count']}/{r['denominator']} | {r['pct']:.1f}% |" if r['pct'] is not None else f"| {r['label']} | 0/0 | 자료 없음 |")
    for r in result['target_groups']:
        lines+=['',f"### {r['label']} 마지막 매도",'','| 단계 | 사유 | 건수 |','|---|---|---:|']
        lines += [f"| {v['stage']} | {v['reason']} | {v['count']} |" for v in r['final_sales']]
    lines+=['','## 하락장 신규 매수 비교','',f"직전 저장 거래일 종가 < MA60 이고 MA20 < MA60. 현재일 종가는 판정에 사용하지 않는다. 지수 {result['index_first']}–{result['index_last']}, 하락장 매수 완료 {len(selected)}건, 비하락장 {result['bear_nonbear']}건, 판정 불가 {unknown}건.",'','| 규칙 | 거래 | 원래 배정자금 대비 평균 순손익 | 기존 대비 | 최저 거래 기여 | 개선/동일/악화 |','|---|---:|---:|---:|---:|---:|']
    for r in comparison:
        if r['n']:lines.append(f"| {r['label']} | {r['n']} | {r['mean_pct']:+.3f}% | {r['mean_difference_pctp']:+.3f}%p | {r['worst_pct']:+.3f}% | {r['improved']}/{r['neutral']}/{r['worsened']} |")
    lines+=['','매수 축소는 정수 수량 내림, 기존 분할 매도는 누적 비례 내림으로 배분한다. 남긴 자금은 무이자 현금이며 다른 종목에 재투자하지 않는다. 조기 익절은 기존 +5% 절반 익절 시뮬레이터를 사용하며 기존 최종 매도에서 비교를 종료한다. 비교군은 각각 독립 적용한다.','', '이는 동일 거래 기회에 배정했던 원래 자금 대비 손익 기여 비교다. 계좌 최대낙폭·전체 현금 비중·회복 후 20거래일 신규 추천의 기회비용은 계산하지 않았다. 계좌 경로와 당시 전체 후보군의 일별 자료 확인이 필요한 별도 단계다.','', '새 관측부터 매도 평가의 목표 1·2 비율, 익절 허용 여부, 분할 비율을 DB에 저장하고, 매수 관측에는 진입 당시 목표 설정을 저장한다. 과거 NULL은 현재 설정으로 채우지 않는다. 운영 매매 판단은 변경하지 않는다.']
    (target.parent/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    r=run()
    print(json.dumps({k:r[k] for k in ('included','target_groups','target_evidence','bear_comparisons','bear_unknown','bear_nonbear','bear_episodes')},ensure_ascii=True))
