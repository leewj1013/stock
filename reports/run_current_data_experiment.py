"""Freeze available observations and compare the already registered exit rules."""
import csv
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from stock_alarm.profit_research import LABELS, DIP_LABELS
from stock_alarm.research_followup import replay_closed


def summarize(cases, key=None):
    values=[r['baseline_pct']+(r['differences_pctp'][key] if key else 0) for r in cases]
    n=len(values)
    assert n and all(isinstance(v,(int,float)) for v in values)
    return {'n':n,'mean_pct':statistics.mean(values),'median_pct':statistics.median(values),
            'profitable':sum(v>0 for v in values),'win_rate_pct':100*sum(v>0 for v in values)/n,
            'worst_pct':min(values)}


def main():
    target=Path('reports/current_data_experiment');target.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(Path('data/stock_alarm.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        buys=[dict(r) for r in db.execute('SELECT * FROM virtual_trades ORDER BY created_at,trade_id')]
        sales=[dict(r) for r in db.execute('SELECT * FROM virtual_sales ORDER BY created_at,sale_id')]
        checks=[dict(r) for r in db.execute('SELECT DISTINCT ticker,checked_at,close FROM position_checks WHERE close>0 AND return_pct IS NOT NULL ORDER BY checked_at')]
    result=replay_closed(buys,checks,sales);cases=result['cases']
    assert len(cases)+sum(result['excluded'].values())==len(buys)
    baseline=summarize(cases)
    rows=[{'key':'baseline','label':'기존 매도',**baseline,'mean_difference_pctp':0,'improved':0,'worsened':0,'neutral':len(cases)}]
    for row in result['comparisons']:
        row={**row,**summarize(cases,row['key'])}
        row['neutral']=len(cases)-row['improved']-row['worsened']
        assert abs(row['mean_pct']-baseline['mean_pct']-row['mean_difference_pctp'])<1e-8
        eligible=[r for r in cases if r['first_take_profit']]
        if row['key'] in ('principal','trail'):
            row['eligible_mean_difference_pctp']=statistics.mean(r['differences_pctp'][row['key']] for r in eligible) if eligible else None
        rows.append(row)
    thresholds=[];handling=Counter()
    for threshold in (0,-3,-5):
        hit=[];rebounded=0
        for case in cases:
            trade=next(r for r in buys if r['trade_id']==case['trade_id'])
            end=min((r['created_at'] for r in buys if r['ticker']==trade['ticker'] and r['created_at']>trade['created_at']),default='9999')
            sold=[r for r in sales if r['ticker']==trade['ticker'] and trade['created_at']<=r['created_at']<end]
            final=max(r['created_at'] for r in sold)
            tape=sorted([(r['checked_at'],float(r['close'])) for r in checks if r['ticker']==trade['ticker'] and trade['created_at']<r['checked_at']<=final]+[(r['created_at'],float(r['price'])) for r in sold])
            touches=[at for at,price in tape if (price/trade['price']-1)*100<=threshold+1e-9]
            if touches:
                hit.append(case)
                rebounded+=any(at>min(touches) and price>trade['price'] for at,price in tape)
            if threshold==0:
                last=max(sold,key=lambda r:(r['created_at'],r['sale_id']))
                handling[(bool(case['first_take_profit']),last.get('stage') or '없음',last.get('reason') or '미기재')]+=1
        n=len(hit);wins=sum(r['baseline_pct']>0 for r in hit);losses=sum(r['baseline_pct']<0 for r in hit)
        assert wins+losses+sum(r['baseline_pct']==0 for r in hit)==n
        thresholds.append({'threshold_pct':threshold,'hit':n,'of_all_pct':100*n/len(cases),'later_above_entry':rebounded,'profitable_exit':wins,'loss_exit':losses,'profitable_of_hit_pct':100*wins/n if n else None,'loss_of_hit_pct':100*losses/n if n else None})
    result.update(generated_at=datetime.now().astimezone().isoformat(),performance=rows,threshold_outcomes=thresholds,final_sale_handling=[{'tp1':k[0],'stage':k[1],'reason':k[2],'count':v} for k,v in handling.items()])
    (target/'input_snapshot.json').write_text(json.dumps({'buys':buys,'checks':checks,'sales':sales},ensure_ascii=False),encoding='utf-8')
    (target/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (target/'performance.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['label','n','mean_pct','median_pct','win_rate_pct','worst_pct','mean_difference_pctp','improved','neutral','worsened']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    lines=['# 현재 보유 자료 청산 실험',f"작성: {result['generated_at']}",'','과거 관측자료 탐색 연구. 가상 거래별 동일 가중치이며 계좌 수익률이 아니다. 신규 전향 연구와 구분한다.',f"분석 {len(cases)}건 / 종목 {len(set(r['ticker'] for r in cases))}개. 제외: {result['excluded']}",'','| 청산 규칙 | 평균 순수익률 | 중앙값 | 이익 거래 비율 | 최저 거래 수익률 | 기존 대비 평균 차이 | 개선/동일/악화 |','|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['label']} | {r['mean_pct']:.3f}% | {r['median_pct']:.3f}% | {r['win_rate_pct']:.1f}% | {r['worst_pct']:.3f}% | {r['mean_difference_pctp']:+.3f}%p | {r['improved']}/{r['neutral']}/{r['worsened']} |")
    lines+=['','| 관측 하락 기준 | 해당 거래/전체 | 이후 매수가 초과 관측 | 최종 합산 이익 | 최종 합산 손실 |','|---|---:|---:|---:|---:|']
    for r in thresholds:
        lines.append(f"| {r['threshold_pct']}% 이하 | {r['hit']}/{len(cases)} ({r['of_all_pct']:.1f}%) | {r['later_above_entry']} | {r['profitable_exit']} ({r['profitable_of_hit_pct']:.1f}%) | {r['loss_exit']} ({r['loss_of_hit_pct']:.1f}%) |")
    lines+=['','하락 기준은 매수가 대비 저장 가격이며 세 집단은 중복된다. 0% 집단은 선행 상승을 요구하지 않는다. 실험의 0% 복귀 청산은 선행 상승을 요구하므로 두 표의 적용 건수는 다를 수 있다. 이익/손실은 모든 분할 매도를 합한 비용 차감 결과다. 손실 종료가 모두 손절 사유라는 뜻은 아니다.','', '| 1차 익절 체결 여부 | 마지막 매도 단계 | 마지막 매도 사유 | 건수 |','|---|---|---|---:|']
    for r in result['final_sale_handling']:
        lines.append(f"| {'체결' if r['tp1'] else '미체결'} | {r['stage']} | {r['reason']} | {r['count']} |")
    lines+=['','해석: 평균 차이가 가장 큰 규칙도 동일 표본에서 관찰한 후보일 뿐이다. 고정 규칙을 유지한 채 신규 거래에서 재검증한다. 1차 익절 이후 규칙은 실제 1차 익절 체결 거래에만 작동하며, 전체 평균은 미작동 거래도 포함한다.']
    for r in rows:
        if r.get('first_take_profit_eligible'):
            lines.append(f"- {r['label']}: 1차 익절 {r['first_take_profit_eligible']}건에서 평균 차이 {r['eligible_mean_difference_pctp']:+.3f}%p; 추가 청산 {r['extra_exit_cases']}건.")
    lines+=['','검증: 기존 매도 현금흐름 재현, 전체/제외 건수 합계, 개선·동일·악화 합계 및 평균 차이 일치 확인. 입력 스냅샷과 SHA256은 results.json 및 input_snapshot.json에 보존.','', '한계: 저장 관측 사이 고저가·실제 호가 체결·슬리피지는 재현하지 못한다. 매수·매도 각 0.15% 비용, 추가 신호는 다음 저장 가격 체결, 기존 최종 매도 시 잔량 종료. 목표가의 당시 원본이 없어 1·2차 목표 미달 여부는 새로 추정하지 않았다. 실거래 규칙과 전향 연구 시작일은 변경하지 않았다.']
    (target/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'included':len(cases),'excluded':result['excluded'],'performance':rows,'thresholds':thresholds},ensure_ascii=True,indent=2))


if __name__=='__main__':
    assert summarize([{'baseline_pct':-2},{'baseline_pct':4}])['mean_pct']==1
    main()
