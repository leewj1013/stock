"""Resumable KRX month-end evidence, separate from frozen backtest inputs."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .research_data_collect import PATH, KST, connect, status


def month_ends(days,today):
    months={}
    for day in sorted(set(days)):
        if len(day)==8 and day.isdigit() and day[:6]<today.strftime("%Y%m"):
            months[day[:6]]=day
    return sorted(months.values())


def saved_sessions():
    days=set()
    path=Path('data/backtest_expanded/ohlcv/KOSPI.csv')
    if path.exists():
        with path.open(encoding='utf-8-sig',newline='') as f:
            days.update(r['date'].replace('-','') for r in csv.DictReader(f))
    for path in Path('.cache/naver').glob('KOSPI-*.json'):
        try:days.update(str(r[0]) for r in json.loads(path.read_text(encoding='utf-8')) if len(r)>4 and float(r[4])>0)
        except (OSError,ValueError,TypeError):continue
    return days


def legacy_balance_dates():
    path=Path('data/backtest_expanded/short_selling/short_balance_monthend.csv')
    if not path.exists():return set()
    with path.open(encoding='utf-8-sig',newline='') as f:
        return {(r['date'].replace('-',''),r['market']) for r in csv.DictReader(f)}


def pending_requests(days,existing,legacy,backfill):
    industry=days if backfill else days[-1:]
    return [(kind,day,market) for kind,dates in [('industry',industry),('short_balance',days)]
            for day in dates for market in ('KOSPI','KOSDAQ')
            if (kind,day,market) not in existing and
            (kind!='short_balance' or day>='20250301' and (day,market) not in legacy)]


def run(path=PATH,backfill=False,client=None,days=None,legacy=None,now=None):
    now=now or datetime.now(KST)
    days=month_ends(saved_sessions() if days is None else days,now.date())
    with closing(connect(path)) as db:
        existing={tuple(r) for r in db.execute('SELECT kind,requested_date,scope FROM krx_snapshots')}
        requests=pending_requests(days,existing,legacy_balance_dates() if legacy is None else legacy,backfill)
        if not requests:return {'snapshots':0,'rows':0,'failures':0}
        if client is None:
            if not os.environ.get('KRX_ID') or not os.environ.get('KRX_PW'):
                status(db,'krx','authentication','AUTH_REQUIRED',{},now);db.commit()
                return {'snapshots':0,'rows':0,'failures':0,'state':'AUTH_REQUIRED'}
            with contextlib.redirect_stdout(io.StringIO()):
                from pykrx import stock
            client=stock
        added=rows=failures=consecutive_failures=0
        for kind,day,market in requests:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    frame=(client.get_market_sector_classifications(day,market) if kind=='industry'
                           else client.get_shorting_balance_by_ticker(day,market))
                if frame.empty:raise ValueError('Empty KRX result')
                payload=frame.reset_index().to_json(orient='records',force_ascii=True,date_format='iso')
                stamp=datetime.now(KST)
                added+=db.execute('INSERT OR IGNORE INTO krx_snapshots VALUES(?,?,?,?,?)',
                                  (kind,day,market,stamp.isoformat(),payload)).rowcount
                rows+=len(frame)
                status(db,'krx',kind+'_'+market+'_'+day,'COLLECTED',{'rows':len(frame),'historical_vintage_verified':False},stamp)
                db.commit()
                consecutive_failures=0
            except Exception as error:
                db.rollback();failures+=1
                consecutive_failures+=1
                status(db,'krx',kind+'_'+market+'_'+day,'ERROR',{'type':type(error).__name__},datetime.now(KST));db.commit()
                if added==0 or consecutive_failures>=3:break
            if (added+failures)%10==0:print(json.dumps({'snapshots':added,'rows':rows,'failures':failures}),flush=True)
            time.sleep(.2)
    return {'snapshots':added,'rows':rows,'failures':failures}


if __name__=='__main__':
    import sys
    from .app import load_env
    load_env()
    print(json.dumps(run(backfill='--backfill' in sys.argv),ensure_ascii=False))
