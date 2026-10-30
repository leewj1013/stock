"""Stock halt and price-basis review gate. Never adjusts quantities or cost."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from .account_valuation import observation_time
from .market_sessions import KST, configured, day_info

CACHE_PATH=Path('data/stock_events.db')


def read_cache():
    try:
        if not CACHE_PATH.is_file():return {'stocks':{}}
        with closing(sqlite3.connect(CACHE_PATH.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            return {'stocks':{t:json.loads(payload) for t,payload in db.execute('SELECT ticker,payload FROM stock_state')}}
    except (OSError,ValueError,sqlite3.DatabaseError):
        return {'stocks':{}}


def save_cache(data):
    CACHE_PATH.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(CACHE_PATH)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS stock_state(ticker TEXT PRIMARY KEY,payload TEXT)')
        db.execute('BEGIN IMMEDIATE')
        for ticker,item in data.get('stocks',{}).items():
            found=db.execute('SELECT payload FROM stock_state WHERE ticker=?',(ticker,)).fetchone()
            previous=json.loads(found[0]) if found else {}
            item=dict(item)
            ack=item.pop('_ack_fingerprint',None)
            if ack and ack!=previous.get('review_fingerprint'):
                raise ValueError('검토 지문이 변경되었습니다.')
            if not ack:
                review=sorted(set(previous.get('review_reasons',[]))|set(item.get('review_reasons',[])))
                evidence={e['id']:e for e in previous.get('review_events',[])+item.get('review_events',[])}
                if (previous.get('checked_at') or '')>(item.get('checked_at') or ''):
                    item=previous
                item['review_reasons']=review
                item['review_events']=list(evidence.values())
            item['review_fingerprint']=fingerprint(ticker,item)
            db.execute('INSERT OR REPLACE INTO stock_state VALUES(?,?)',(ticker,json.dumps(item,ensure_ascii=False)))


def fingerprint(ticker,item):
    return hashlib.sha256(json.dumps([ticker,item.get('review_reasons',[]),item.get('review_events',[]),
                                     item.get('info',{}).get('isinCode')],sort_keys=True).encode()).hexdigest()[:16]


def add_evidence(item,evidence,now):
    key=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()[:16]
    events=item.setdefault('review_events',[])
    if not any(e['id']==key for e in events):events.append({**evidence,'id':key,'observed_at':now.isoformat()})


def prior_close(payload, day):
    rows=[c for c in payload.get('candles',[]) if c.get('timestamp','')[:10]==day]
    if len(rows)!=1:
        raise ValueError('previous_session_candle_missing')
    price=float(rows[0]['closePrice'])
    if not 0<price<float('inf'):
        raise ValueError('invalid_previous_close')
    return price


def inspect(tickers, holdings=None, client=None, now=None, force=False, disclosures_fn=None):
    now=(now or datetime.now(KST)).astimezone(KST);holdings=holdings or {}
    data=read_cache();stocks=data.setdefault('stocks',{})
    due=[t for t in sorted(set(tickers)) if force or not observation_time(stocks.get(t,{}).get('checked_at'))
         or (now-observation_time(stocks[t]['checked_at'])).total_seconds()>300
         or (holdings.get(t,{}).get('first_entry_at') or now.date().isoformat())[:10]<now.date().isoformat()
            and stocks.get(t,{}).get('basis_checked_date')!=now.date().isoformat()]
    if not due:
        return data
    if client is None:
        from .toss_client import shared_client
        client=shared_client()
    rows=[]
    try:
        for offset in range(0,len(due),200):
            rows.extend(client.stocks(due[offset:offset+200]))
    except Exception as error:
        for t in due:
            stocks.setdefault(t,{})['lookup_error']=type(error).__name__
        save_cache(data);return read_cache()
    for ticker in due:
        old=stocks.get(ticker,{})
        matches=[r for r in rows if isinstance(r,dict) and r.get('symbol')==ticker]
        if len(matches)!=1:
            stocks[ticker]={**old,'lookup_error':'missing_or_duplicate_stock'};continue
        row=matches[0];detail=row.get('koreanMarketDetail') or {}
        current={**old,'checked_at':now.isoformat(),'info':row,'lookup_error':'',
                 'review_reasons':list(old.get('review_reasons',[]))}
        if old.get('info',{}).get('isinCode') and row.get('isinCode')!=old['info']['isinCode']:
            current['review_reasons'].append('종목 식별번호 변경 · 보유 수량/원가 확인 필요')
            add_evidence(current,{'kind':'isin_changed','old':old['info']['isinCode'],'new':row.get('isinCode')},now)
        holding=holdings.get(ticker,{})
        carried=bool(holding.get('first_entry_at') and holding['first_entry_at'][:10]<now.date().isoformat())
        if carried and current.get('basis_checked_date')!=now.date().isoformat():
            calendar=day_info(now)
            try:
                if not calendar or not calendar['open']:
                    raise ValueError('calendar_unverified')
                day=calendar['previous_date'];before=day+'T23:59:59+09:00'
                adjusted=prior_close(client.candles(ticker,'1d',2,before=before,adjusted=True),day)
                raw=prior_close(client.candles(ticker,'1d',2,before=before,adjusted=False),day)
                current.update(basis_checked_date=now.date().isoformat(),basis_error='',
                               basis_evidence={'date':day,'adjusted_close':adjusted,'raw_close':raw})
                if abs(adjusted/raw-1)>.005:
                    current['review_reasons'].append('수정/비수정 가격 기준 변경 · 권리락/분할 등 원가·수량 확인 필요')
                    add_evidence(current,{'kind':'price_basis_changed',**current['basis_evidence']},now)
            except Exception as error:
                current['basis_error']=type(error).__name__
        calendar=day_info(now)
        phase='after_close' if calendar and calendar['open'] and now>=calendar['end']+timedelta(minutes=30) else 'before_close'
        if carried and (current.get('disclosure_date')!=now.date().isoformat() or current.get('disclosure_phase')!=phase):
            current['advisories']=[]
            if disclosures_fn is not None or os.environ.get('DART_API_KEY'):
                try:
                    if disclosures_fn is None:
                        from .dart_reference import disclosures
                        disclosures_fn=disclosures
                    found=disclosures_fn(ticker,days=90,limit=100)
                    keywords=('주식분할','주식병합','감자결정','회사분할','회사합병','권리락','배당락','거래정지')
                    current['advisories']=[{'title':r.get('report_nm',''),'receipt':r.get('rcept_no','')}
                        for r in found if any(k in r.get('report_nm','').replace(' ','') for k in keywords)]
                    current['disclosure_date']=now.date().isoformat()
                    current['disclosure_phase']=phase
                    current['disclosure_note']='최근 공시 제목 참고 · 효력일 미확인'
                except Exception:
                    current['disclosure_note']='공시 제목 조회 실패 · 효력일 미확인'
            else:
                current['disclosure_note']='DART 미연결 · 기업행동 공시 확인 필요'
        current['review_reasons']=sorted(set(current['review_reasons']))
        current['review_fingerprint']=fingerprint(ticker,current)
        stocks[ticker]=current
    save_cache(data);return read_cache()


def reasons(ticker, now=None, data=None, carried=False):
    now=(now or datetime.now(KST)).astimezone(KST)
    item=(data or read_cache()).get('stocks',{}).get(ticker,{})
    result=list(item.get('review_reasons',[]))
    checked=observation_time(item.get('checked_at'))
    if not checked or checked.date()!=now.date() or (now-checked).total_seconds()>300 or item.get('lookup_error'):
        return result+['종목 거래상태 확인 불가']
    info=item.get('info',{});detail=info.get('koreanMarketDetail') or {}
    if info.get('status')!='ACTIVE':result.append('비활성/상장 상태 확인 필요')
    if detail.get('krxTradingSuspended') is not False:result.append('KRX 거래정지 또는 정지 여부 미확인')
    if carried and (item.get('basis_checked_date')!=now.date().isoformat() or item.get('basis_error')):
        result.append('기업행동 가격기준 확인 불가')
    return result


def display(ticker,carried=False):
    data=read_cache();item=data.get('stocks',{}).get(ticker,{})
    review=reasons(ticker,data=data,carried=carried) if item or configured() else []
    from .market_sessions import regular_allowed
    if item and regular_allowed() is False and not item.get('lookup_error'):
        review=list(item.get('review_reasons',[]))
        info=item.get('info',{})
        if info.get('status')!='ACTIVE':review.append('비활성/상장 상태 확인 필요')
        if (info.get('koreanMarketDetail') or {}).get('krxTradingSuspended') is not False:review.append('KRX 거래정지 또는 정지 여부 미확인')
    return {'reasons':review,'advisories':item.get('advisories',[]),
            'checked_at':item.get('checked_at',''),'note':item.get('disclosure_note',''),
            'fingerprint':item.get('review_fingerprint','')}


def blocked(tickers, holdings=None, client=None, now=None):
    now=(now or datetime.now(KST)).astimezone(KST);holdings=holdings or {}
    if client is None and not configured():
        data=read_cache()
        tickers=[t for t in tickers if t in data.get('stocks',{})]
    else:
        data=inspect(tickers,holdings,client,now)
    result={}
    for ticker in tickers:
        entry=holdings.get(ticker,{}).get('first_entry_at') or ''
        review=reasons(ticker,now,data,bool(entry and entry[:10]<now.date().isoformat()))
        if review:result[ticker]=review
    return result


def acknowledge(ticker, fingerprint, note):
    if not note.strip():raise ValueError('확인 내용이 필요합니다.')
    data=read_cache();item=data.get('stocks',{}).get(ticker,{})
    if not item.get('review_reasons') or item.get('review_fingerprint')!=fingerprint:
        raise ValueError('검토 대상 또는 지문이 변경되었습니다.')
    item['acknowledged']={'at':datetime.now(KST).isoformat(),'note':note,'fingerprint':fingerprint}
    item['review_reasons']=[];item['_ack_fingerprint']=fingerprint;save_cache({'stocks':{ticker:item}})


def main():
    import argparse
    from .app import load_env
    from .data_store import virtual_trader_state,query_rows
    from .trading_profiles import PROFILES
    load_env();parser=argparse.ArgumentParser();parser.add_argument('--ack');parser.add_argument('--fingerprint');parser.add_argument('--note',default='');args=parser.parse_args()
    if args.ack:
        acknowledge(args.ack,args.fingerprint,args.note);return
    holdings={}
    for p in PROFILES.values():
        if Path(p['db_path']).is_file():
            for h in virtual_trader_state(path=p['db_path'])['holdings']:
                if h['ticker'] not in holdings or (h.get('first_entry_at') or '')<(holdings[h['ticker']].get('first_entry_at') or ''):holdings[h['ticker']]=h
    selected=query_rows("SELECT DISTINCT ticker FROM profile_candidate_selections WHERE selected=1 AND run_id=(SELECT run_id FROM strategy_runs WHERE status='completed' AND run_type='recommendation' ORDER BY started_at DESC LIMIT 1)")
    tickers=sorted(set(holdings)|{r['ticker'] for r in selected})
    data=inspect(tickers,holdings)
    print(json.dumps({'checked':len(tickers),'review':sum(bool(v.get('review_reasons')) for v in data['stocks'].values()),'unavailable':sum(bool(v.get('lookup_error')) for v in data['stocks'].values())}))


if __name__=='__main__':main()
