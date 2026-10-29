"""Frozen-ledger account replay and a predefined recovery opportunity benchmark."""
from __future__ import annotations

import hashlib
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from tempfile import NamedTemporaryFile

from .target_bear_research import bear_labels, previous_label

DIRECTORY = Path('reports/bear_account_research')
RULES = {'version':'bear-account-ledger-v2-common-limit','fee_bps_each_side':15,
         'half':'floor_original_quantity_divided_by_2','buy_opportunities':'recorded_actual_buys_only',
         'sale_allocation':'cumulative_floor_of_recorded_quantity','cash_limit':'clip_integer_quantity_no_borrowing',
         'reentry':'completed_aggressive_recommendations_next_session_open',
         'reentry_budget':'positive_skip_minus_baseline_cash_at_recovery_close_divided_by_7',
         'reentry_max_names':7,'recovery_sessions':20,'reentry_exit':'20th_session_close',
         'all_account_max_names':7,'original_buys':'same_seven_name_limit_in_all_four_accounts',
         'return':'cashflow_unitized_time_weighted','mdd':'daily_unit_nav_close_to_close'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def recovery_windows(points):
    labels=bear_labels(points);days=[d for d,_ in points];result=[]
    for i,day in enumerate(days):
        if i and day in labels and days[i-1] in labels and labels[days[i-1]]['bear'] and not labels[day]['bear']:
            end=i+RULES['recovery_sessions']
            result.append({'recovery':day,'end':days[end] if end<len(days) else None,
                           'available_sessions':min(RULES['recovery_sessions'],len(days)-i-1)})
    return result


def assign_sales(buys,sales):
    assigned={};used=Counter()
    for sale in sales:
        eligible=[b for b in buys if b['ticker']==sale['ticker'] and b['created_at']<=sale['created_at']]
        if not eligible:raise ValueError('Sale without entry')
        trade=max(eligible,key=lambda b:(b['created_at'],b['trade_id']))
        used[trade['trade_id']]+=int(sale['quantity'])
        if used[trade['trade_id']]>int(trade['quantity']):raise ValueError('Overlapping or oversold entry')
        assigned[sale['sale_id']]=trade['trade_id']
    for trade in buys:
        earlier=[b for b in buys if b['ticker']==trade['ticker'] and b['created_at']<trade['created_at']]
        if earlier:
            prior=max(earlier,key=lambda b:b['created_at'])
            sold=sum(s['quantity'] for s in sales if s['ticker']==trade['ticker'] and prior['created_at']<=s['created_at']<trade['created_at'])
            if sold!=prior['quantity']:raise ValueError('Entry overlaps an unfinished lot')
    return assigned


def replay_account(source,mode,*,fee_bps=15,recovery=None,position_limit=7):
    """Orders keep original opportunities and exits; freed cash is not reallocated implicitly."""
    points=source['index'];days=[d for d,_ in points if source['start']<=d<=source['cutoff']]
    labels=bear_labels(points);prices=source['prices'];fee=fee_bps/10000
    buys=source['buys'];sales=source['sales'];mapping=assign_sales(buys,sales)
    events=[]
    for dep in source['deposits']:events.append((dep['created_at'],0,'deposit',dep))
    for sale in sales:events.append((sale['created_at'],2,'sale',sale))
    for buy in buys:events.append((buy['created_at'],3,'buy',buy))
    for day in days:events.append((f'{day[:4]}-{day[4:6]}-{day[6:]}T15:30:00',4,'close',{'day':day}))
    for window in recovery or []:
        for signal in window['signals']:
            iso=f"{signal['entry_day'][:4]}-{signal['entry_day'][4:6]}-{signal['entry_day'][6:]}"
            events.append((iso+'T09:00:00',1,'reentry',{**signal,'window':window['recovery'],
                'budget':window['budget'],'end':window['end']}))
        day=window['end'];iso=f'{day[:4]}-{day[4:6]}-{day[6:]}'
        events.append((iso+'T15:30:01',5,'reentry_exit',{'window':window['recovery'],'day':day}))
    events.sort(key=lambda r:(r[0],r[1],r[3].get('known_at',''),-(r[3].get('profile_score') or 0),
                              r[3].get('sale_id',r[3].get('trade_id',0)),r[3].get('ticker','')))
    cash=units=0.0;lots={};marks={};original_sold=Counter();allocated=Counter();curve=[];audit=[]
    window_spent=Counter();window_names=defaultdict(set);previous_day=None

    def equity():return cash+sum(lot['remaining']*marks[lot['ticker']] for lot in lots.values() if lot['remaining'])
    def price(ticker,day,field):
        value=prices.get(ticker,{}).get(day)
        if value is None:raise ValueError(f'Missing price: {ticker}/{day}')
        number=float(value[0 if field=='open' else 1])
        if not math.isfinite(number) or number<=0:raise ValueError('Invalid saved price')
        return number
    def settle_day(day):
        value=equity();nav=value/units if units else 1.0
        row={'day':day,'cash':cash,'holdings_value':value-cash,'equity':value,'units':units,'nav':nav,
             'cash_pct':cash/value*100 if value else None,
             'holdings':{str(k):{'ticker':v['ticker'],'quantity':v['remaining']} for k,v in lots.items() if v['remaining']}}
        curve.append(row)
    for at,_,kind,row in events:
        day=at[:10].replace('-','')
        if day>source['cutoff']:continue
        if previous_day and day!=previous_day and previous_day>=source['start']:settle_day(previous_day)
        previous_day=day
        if kind=='deposit':
            if any(lot['remaining'] for lot in lots.values()) and at[11:]<'15:30:00':
                raise ValueError('Intraday deposit requires an observed pre-flow portfolio valuation')
            nav=equity()/units if units else 1.0
            if nav<=0:raise ValueError('Nonpositive deposit NAV')
            units+=float(row['amount'])/nav;cash+=float(row['amount'])
        elif kind=='buy':
            qty=int(row['quantity']);requested=qty;label=previous_label(at,points,labels)
            if mode!='baseline' and label is None:raise ValueError('Unknown entry regime')
            if label and label['bear']:
                if mode=='half':qty//=2
                elif mode in ('skip','resume'):qty=0
            requested=qty
            held={v['ticker'] for v in lots.values() if v['remaining']}
            holding_limited=position_limit is not None and row['ticker'] not in held and len(held)>=position_limit and qty>0
            if holding_limited:qty=0
            cash_request=qty;qty=min(qty,max(0,math.floor((cash+1e-7)/(row['price']*(1+fee)))))
            cash-=qty*row['price']*(1+fee);marks[row['ticker']]=float(row['price'])
            lots[row['trade_id']]={'ticker':row['ticker'],'original_quantity':int(row['quantity']),
                                  'quantity':qty,'remaining':qty}
            audit.append({'at':at,'kind':'buy','trade_id':row['trade_id'],'ticker':row['ticker'],
                          'requested':requested,'filled':qty,'cash_clipped':qty<cash_request,'holding_limited':holding_limited})
        elif kind=='sale':
            key=mapping[row['sale_id']];lot=lots[key];original_sold[key]+=int(row['quantity'])
            target=original_sold[key]*lot['quantity']//lot['original_quantity'];qty=target-allocated[key]
            if qty>lot['remaining']:raise ValueError('Sale exceeds simulated holding')
            allocated[key]=target;lot['remaining']-=qty;cash+=qty*row['price']*(1-fee)
            marks[row['ticker']]=float(row['price'])
        elif kind=='close':
            for ticker in {v['ticker'] for v in lots.values() if v['remaining']}:marks[ticker]=price(ticker,day,'close')
        elif kind=='reentry':
            if row['known_at']>=at:raise ValueError('Reentry precedes its known signal')
            ticker=row['ticker'];window=row['window'];reason=None
            held={v['ticker'] for v in lots.values() if v['remaining']}
            if ticker in held or ticker in window_names[window]:reason='already_held_or_entered'
            elif len(held)>=RULES['reentry_max_names']:reason='seven_name_limit'
            elif prices.get(ticker,{}).get(day) is None:reason='missing_entry_price'
            label=previous_label(at,points,labels)
            if label is None or label['bear']:reason='entry_regime_not_recovered'
            if not reason:
                opening=price(ticker,day,'open');budget=min(cash,row['budget']/7,max(0,row['budget']-window_spent[window]))
                qty=max(0,math.floor((budget+1e-7)/(opening*(1+fee))))
                if not qty:reason='below_one_share_or_cash_budget'
                else:
                    cost=qty*opening*(1+fee);cash-=cost;window_spent[window]+=cost;window_names[window].add(ticker)
                    marks[ticker]=opening;key=f'recovery:{window}:{ticker}'
                    lots[key]={'ticker':ticker,'quantity':qty,'remaining':qty,'window':window}
                    audit.append({'at':at,'kind':'reentry','ticker':ticker,'quantity':qty,'price':opening,
                                  'cost':cost,'signal_known_at':row['known_at'],'window':window})
            if reason:audit.append({'at':at,'kind':'reentry_excluded','ticker':ticker,'reason':reason,'window':window})
        elif kind=='reentry_exit':
            for lot in lots.values():
                if lot.get('window')==row['window'] and lot['remaining']:
                    closing=price(lot['ticker'],day,'close');qty=lot['remaining'];lot['remaining']=0
                    cash+=qty*closing*(1-fee);marks[lot['ticker']]=closing
                    audit.append({'at':at,'kind':'reentry_exit','ticker':lot['ticker'],'quantity':qty,'price':closing,
                                  'window':row['window']})
        if cash< -1e-6:raise ValueError('Borrowed cash')
        if position_limit is not None and len({v['ticker'] for v in lots.values() if v['remaining']})>position_limit:
            raise ValueError('Account position limit exceeded')
    if previous_day and previous_day>=source['start']:settle_day(previous_day)
    curve=[r for r in curve if r['day'] in days]
    if [r['day'] for r in curve]!=days:raise ValueError('Missing account valuation sessions')
    peak=1.0;mdd=0.0
    for row in curve:
        peak=max(peak,row['nav']);mdd=min(mdd,row['nav']/peak-1)
        row['drawdown_pct']=(row['nav']/peak-1)*100
    final=curve[-1]
    return {'mode':mode,'fee_bps':fee_bps,'daily':curve,'audit':audit,
            'metrics':{'sessions':len(curve),'twr_pct':(final['nav']-1)*100,'mdd_pct':mdd*100,
                       'equity':final['equity'],'cash':final['cash'],'cash_pct':final['cash_pct'],
                       'average_cash_pct':mean(r['cash_pct'] for r in curve),
                       'cash_clipped_buys':sum(r.get('cash_clipped',False) for r in audit),
                       'holding_limited_buys':sum(r.get('holding_limited',False) for r in audit),
                       'maximum_held_names':max(len({v['ticker'] for v in r['holdings'].values()}) for r in curve)}}


def cached_prices(tickers,start,cutoff):
    files=defaultdict(list)
    for path in Path('.cache/naver').glob('*.json'):
        ticker=path.stem.split('-')[0]
        if ticker in tickers:files[ticker].append(path)
    prices={};provenance={};issues=[]
    for ticker in sorted(tickers):
        selected=sorted((p for p in files[ticker] if p.stem.split('-')[-1]>=start and p.stem.split('-')[-2]<=cutoff),
                        key=lambda p:(p.stem.split('-')[-1],-int(p.stem.split('-')[-2])),reverse=True)
        merged={};used=[]
        for path in selected:
            try:rows=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):issues.append({'file':str(path),'reason':'unreadable'});continue
            count=0
            for row in rows:
                if not isinstance(row,list) or len(row)<6:continue
                day=str(row[0])
                if not start<=day<=cutoff or day in merged:continue
                opening,high,low,close,volume=map(float,row[1:6])
                if not all(math.isfinite(v) for v in (opening,high,low,close,volume)) or min(opening,high,low,close)<=0 or volume<0 or high<max(opening,close) or low>min(opening,close):
                    issues.append({'ticker':ticker,'day':day,'reason':'invalid_ohlcv'});continue
                merged[day]=[opening,close];count+=1
            if count:used.append({'file':str(path),'rows':count})
        prices[ticker]=merged;provenance[ticker]=used
    return prices,provenance,issues


def collect_inputs(source='data/stock_alarm.db',cutoff=None):
    from .dashboard_v2 import _index_cache
    cutoff=cutoff or (date.today()-timedelta(days=1)).strftime('%Y%m%d')
    points=[list(r) for r in _index_cache('KOSPI') if r[0]<=cutoff]
    if not points:raise ValueError('No completed index sessions')
    cutoff=points[-1][0];iso=f'{cutoff[:4]}-{cutoff[4:6]}-{cutoff[6:]}'
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        buys=[dict(r) for r in db.execute('SELECT * FROM virtual_trades WHERE created_at<? ORDER BY created_at,trade_id',(iso+'T23:59:59',))]
        sales=[dict(r) for r in db.execute('SELECT * FROM virtual_sales WHERE created_at<? ORDER BY created_at,sale_id',(iso+'T23:59:59',))]
        deposits=[dict(r) for r in db.execute('SELECT * FROM virtual_deposits WHERE created_at<? ORDER BY created_at,deposit_id',(iso+'T23:59:59',))]
        start=min(b['created_at'][:10].replace('-','') for b in buys)
        windows=[w for w in recovery_windows(points) if w['recovery']>=start and w['end']]
        signals=[];failed=0
        for window in windows:
            day=window['recovery'];end=window['end'];begin=f'{day[:4]}-{day[4:6]}-{day[6:]}'
            finish=f'{end[:4]}-{end[4:6]}-{end[6:]}'
            for row in db.execute("""SELECT c.ticker,c.name,c.evaluated_at,p.profile_score,r.run_id,r.status,r.finished_at
                FROM profile_candidate_selections p JOIN candidate_snapshots c ON c.run_id=p.run_id AND c.ticker=p.ticker
                JOIN strategy_runs r ON r.run_id=p.run_id WHERE p.profile='aggressive' AND p.selected=1 AND c.passed=1
                AND c.evaluated_at>=? AND c.evaluated_at<? ORDER BY r.finished_at,p.profile_score DESC,c.ticker""",(begin,finish)):
                row=dict(row)
                if row['status']!='completed' or not row['finished_at']:failed+=1;continue
                known=max(row['evaluated_at'],row['finished_at'],begin+'T15:30:00')
                following=[d for d,_ in points if d>known[:10].replace('-','')]
                if following and following[0]<=end:
                    signals.append({**row,'known_at':known,'entry_day':following[0],'window':day})
    tickers={r['ticker'] for r in buys}|{s['ticker'] for s in signals}
    prices,provenance,issues=cached_prices(tickers,start,cutoff)
    return {'created_at':datetime.now().astimezone().isoformat(),'rules':RULES,'start':start,'cutoff':cutoff,
            'index':points,'buys':buys,'sales':sales,'deposits':deposits,'signals':signals,
            'windows':windows,'excluded_failed_signal_rows':failed,'prices':prices,
            'price_provenance':provenance,'price_issues':issues}


def run(source='data/stock_alarm.db',directory=DIRECTORY):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    inputs=collect_inputs(source)
    if inputs['rules']!=RULES:raise ValueError('Input snapshot rules differ')
    raw=replay_account(inputs,'baseline',fee_bps=0,position_limit=None)
    expected=sum(r['amount'] for r in inputs['deposits'])-sum(r['cost'] for r in inputs['buys'])+sum(r['proceeds'] for r in inputs['sales'])
    if abs(raw['metrics']['cash']-expected)>1e-6:raise ValueError('Recorded cash ledger does not reconcile')
    holdings=Counter()
    for row in inputs['buys']:holdings[row['ticker']]+=row['quantity']
    for row in inputs['sales']:holdings[row['ticker']]-=row['quantity']
    actual=Counter()
    for row in raw['daily'][-1]['holdings'].values():actual[row['ticker']]+=row['quantity']
    if +holdings!=actual:raise ValueError('Recorded remaining shares do not reconcile')
    accounts={mode:replay_account(inputs,mode) for mode in ('baseline','half','skip')}
    curves={key:{r['day']:r for r in value['daily']} for key,value in accounts.items()}
    windows=[]
    for window in inputs['windows']:
        day=window['recovery'];budget=max(0,curves['skip'][day]['cash']-curves['baseline'][day]['cash'])
        windows.append({**window,'budget':budget,'signals':[s for s in inputs['signals'] if s['window']==day]})
    accounts['resume']=replay_account(inputs,'resume',recovery=windows)
    opportunities=[]
    for window in windows:
        day,end=window['recovery'],window['end'];comparison={}
        for key,account in accounts.items():
            series=[r for r in account['daily'] if day<=r['day']<=end];peak=series[0]['nav'];mdd=0
            for row in series:
                peak=max(peak,row['nav']);mdd=min(mdd,row['nav']/peak-1)
            comparison[key]={'window_twr_pct':(series[-1]['nav']/series[0]['nav']-1)*100,
                             'window_mdd_pct':mdd*100,'end_equity':series[-1]['equity']}
        exits=[r for r in accounts['resume']['audit'] if r['kind']=='reentry_exit' and r['window']==day]
        entries=[r for r in accounts['resume']['audit'] if r['kind']=='reentry' and r['window']==day]
        pnl=sum(r['quantity']*r['price']*.9985 for r in exits)-sum(r['cost'] for r in entries)
        opportunities.append({**{k:v for k,v in window.items() if k!='signals'},'eligible_signal_rows':len(window['signals']),
                              'reentry_count':len(entries),'reentry_net_pnl':pnl,
                              'reentry_cost':sum(r['cost'] for r in entries),
                              'resume_minus_skip_equity':comparison['resume']['end_equity']-comparison['skip']['end_equity'],
                              'accounts':comparison})
    result={'generated_at':datetime.now().astimezone().isoformat(),'scope':'HISTORICAL_FIXED_LEDGER_ACCOUNT_REPLAY',
            'rules':RULES,'source_sha256':digest(inputs),'start':inputs['start'],'cutoff':inputs['cutoff'],
            'buys':len(inputs['buys']),'sales':len(inputs['sales']),'deposits':inputs['deposits'],
            'gross_cash_reconciliation_error':raw['metrics']['cash']-expected,
            'accounts':accounts,'recovery':opportunities,'excluded_failed_signal_rows':inputs['excluded_failed_signal_rows'],
            'price_issues':inputs['price_issues']}
    bundle=directory/'runs'/(datetime.now().strftime('%Y%m%dT%H%M%S%f')+'-'+result['source_sha256'][:12])
    bundle.mkdir(parents=True,exist_ok=False);result['artifact_directory']=str(bundle)
    (bundle/'inputs.json').write_text(json.dumps(inputs,ensure_ascii=False),encoding='utf-8')
    (bundle/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (bundle/'daily_accounts.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=['account','day','equity','cash','cash_pct','nav','drawdown_pct'],extrasaction='ignore')
        writer.writeheader()
        for key,account in accounts.items():writer.writerows({'account':key,**row} for row in account['daily'])
    write_report(result,bundle)
    for name in ('inputs.json','daily_accounts.csv','REPORT.md','results.json'):
        with NamedTemporaryFile(dir=directory,delete=False) as file:
            temporary=Path(file.name);file.write((bundle/name).read_bytes())
        try:temporary.replace(directory/name)
        finally:temporary.unlink(missing_ok=True)
    return result


def write_report(result,directory):
    labels={'baseline':'기존 매수 기회','half':'하락장 매수 수량 절반','skip':'하락장 매수 보류','resume':'하락장 보류 + 회복 후 추천 재진입'}
    lines=['# 하락장 대체 계좌·회복 기회비용',f"작성 {result['generated_at']}",'',
           f"기간 {result['start']}–{result['cutoff']}. 매수 {result['buys']}건, 매도 {result['sales']}건. 오늘 장중 자료는 제외했다.",
           '원장 수수료 미반영 현금 재현 오차: '+str(result['gross_cash_reconciliation_error'])+'원.',
           '네 계좌 모두 동일하게 최대 7종목을 적용한다. 한도 초과 신규 매수만 보류하며 기존 보유를 강제 청산하지 않는다. 원장 재현 검사는 한도 없는 별도 감사이며 실험 대조군과 구분한다.',
           '실험은 매수·매도 각 0.15% 비용, 정수 수량, 현금 부족 시 수량 축소, 차입 없음. 입금 시점의 단위수를 늘려 입금 영향을 제거한 시간가중수익률(TWR)을 계산한다.',
           '', '| 계좌 | TWR | 일별 최대낙폭 | 마지막 자산 | 평균 현금 비중 | 마지막 현금 비중 | 현금 부족 축소 | 보유 한도 매수 보류 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key,account in result['accounts'].items():
        m=account['metrics'];lines.append(f"| {labels[key]} | {m['twr_pct']:+.3f}% | {m['mdd_pct']:.3f}% | {m['equity']:,.0f}원 | {m['average_cash_pct']:.1f}% | {m['cash_pct']:.1f}% | {m['cash_clipped_buys']}건 | {m['holding_limited_buys']}건 |")
    lines+=['','## 회복 후 20거래일','',
            '회복 당일 종가로 상태를 확인하고, 이후 신규 진입은 성공적으로 완료된 적극투자형 추천의 확인 시각 다음 거래일 시가를 사용한다. 실패한 실행에서 남은 추천 행은 제외한다.',
            '회복일 종료 시 보류 계좌와 기존 계좌의 현금 차이가 양수인 만큼만 재진입 예산으로 묶는다. 종목당 예산 1/7, 전체 최대 7종목, 기존 보유 종목 추가 매수 없음. 회복 후 20번째 거래일 종가에 추가 물량을 청산한다. 기간 중 다시 하락장이 되면 신규 진입은 중단한다. 기존 물량과 실제 매수 기회는 별도 유지한다.',
            '기존·축소·보류·재진입 계좌 모두 원래 매수와 추가 진입에 동일한 전체 7종목 한도를 적용한다. 보류와 재진입의 차이는 같은 한도에서 재진입 정책을 추가한 영향이며, 추가 물량 자체 손익과 계좌 자산 차이는 이후 원래 매수의 제약 효과 때문에 다를 수 있다.',
            '', '| 회복 확인 | 20일 종료 | 재진입 예산 | 적격 추천 행 | 재진입 | 추가 물량 순손익 | 보류 대비 종료 자산 차이 |',
            '|---|---|---:|---:|---:|---:|---:|']
    for w in result['recovery']:
        lines.append(f"| {w['recovery']} | {w['end']} | {w['budget']:,.0f}원 | {w['eligible_signal_rows']} | {w['reentry_count']}건 | {w['reentry_net_pnl']:+,.0f}원 | {w['resume_minus_skip_equity']:+,.0f}원 |")
        lines+=['','| 계좌 | 회복 구간 TWR | 구간 최대낙폭 |','|---|---:|---:|']
        for key,m in w['accounts'].items():lines.append(f"| {labels[key]} | {m['window_twr_pct']:+.3f}% | {m['window_mdd_pct']:.3f}% |")
    lines+=['',f"실패 실행 추천 제외 {result['excluded_failed_signal_rows']}행. 가격 검사 이슈 {len(result['price_issues'])}건.",
            '', '## 해석 범위', '',
            '전체 매수 엔진을 다시 실행한 전략 백테스트가 아니다. 실제 매수 기회를 공통으로 고정한 계좌 경로 비교이며, 매수 보류로 다른 미매수 종목의 선택·순위가 달라지는 효과는 반영하지 않는다.',
            '회복 재진입은 기회비용을 측정하는 사전 고정 벤치마크이며 운영 매도 규칙을 적용한 신규 추천 전략이 아니다. 기존 부분 매도는 누적 비례 내림으로 배분한다.',
            '가격은 저장된 과거 일봉 캐시를 사용하고 가격 빈티지를 보장하지 않는다. 보유일 종가 누락 시 계산을 중단하며 임의로 전일 종가를 채우지 않는다. 일별 낙폭은 장중 최대낙폭이 아니며 시가·종가 체결 가능성, 슬리피지, 배당·기업행동 조정은 별도 검증이 필요하다.',
            '운영 계좌·자동 주문·전향 연구 시작일은 변경하지 않았다. inputs.json과 해시, 계좌별 일별 자산·현금·단위수·낙폭 및 주문 감사 기록을 보존한다.']
    (directory/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    result=run()
    print(json.dumps({'period':[result['start'],result['cutoff']],
                      'metrics':{k:v['metrics'] for k,v in result['accounts'].items()},
                      'recovery':result['recovery']},ensure_ascii=True))
