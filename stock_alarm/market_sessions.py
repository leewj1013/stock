"""Official daily sessions, cached locally; no account or order requests."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CACHE_PATH = Path('data/market_sessions.json')


def local_time(value=None):
    value=value or datetime.now(KST)
    return value.replace(tzinfo=value.tzinfo or KST).astimezone(KST)


def configured():
    return bool(os.environ.get('TOSS_CLIENT_ID') and os.environ.get('TOSS_CLIENT_SECRET'))


def read_cache():
    try:
        return json.loads(CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def stamp(text):
    result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        raise ValueError('calendar_timezone_missing')
    return result.astimezone(KST)


def day_info(now=None):
    now = local_time(now)
    cache = read_cache()
    try:
        if stamp(cache['checked_at']).date() != now.date():
            return None
        day = cache['data']['today']
        if day['date'] != now.date().isoformat():
            return None
        integrated = day.get('integrated')
        regular = integrated.get('regularMarket') if integrated else None
        if not regular:
            return {'open':False, 'checked_at':cache['checked_at']}
        start, end = stamp(regular['startTime']), stamp(regular['endTime'])
        if not (start.date() == end.date() == now.date() and start < end):
            return None
        pre = integrated.get('preMarket')
        pre_start = stamp(pre['startTime']) if pre else start - timedelta(minutes=30)
        pre_end = stamp(pre.get('singlePriceAuctionStartTime') or pre['endTime']) if pre else start - timedelta(minutes=10)
        return {'open':True,'start':start,'end':end,'pre_start':pre_start,'pre_end':pre_end,
                'expected_refresh':start-timedelta(minutes=30), 'checked_at':cache['checked_at'],
                'previous_date':cache['data']['previousBusinessDay']['date'],
                'changed':start.strftime('%H:%M')!='09:00' or end.strftime('%H:%M')!='15:30'}
    except (KeyError, TypeError, ValueError):
        return None


def refresh(now=None, client=None, force=False):
    now = local_time(now)
    cached = read_cache()
    try:
        if not force and day_info(now) and (now-stamp(cached['checked_at'])).total_seconds()<1800:
            return cached
    except (KeyError, ValueError):
        pass
    try:
        if client is None:
            from .toss_client import shared_client
            client = shared_client()
        data = client.market_calendar_kr()
        if data.get('today',{}).get('date') != now.date().isoformat():
            raise ValueError('calendar_date_mismatch')
        # Validate before replacing the last known good cache.
        regular = (data['today'].get('integrated') or {}).get('regularMarket')
        if regular:
            start,end=stamp(regular['startTime']),stamp(regular['endTime'])
            if not (start.date()==end.date()==now.date() and start<end):
                raise ValueError('invalid_regular_session')
        result={'checked_at':now.isoformat(),'data':data}
        CACHE_PATH.parent.mkdir(parents=True,exist_ok=True)
        temp=CACHE_PATH.with_suffix('.tmp');temp.write_text(json.dumps(result),encoding='utf-8');temp.replace(CACHE_PATH)
        return result
    except Exception as error:
        return {**cached, 'refresh_error':type(error).__name__}


def regular_allowed(now=None):
    now=local_time(now)
    info=day_info(now)
    if info is None:
        return False if configured() else None
    return bool(info['open'] and info['start']<=now<=info['end'])


def close_wait_seconds(now=None):
    now=local_time(now)
    info=day_info(now)
    return max(0,int((info['end']+timedelta(minutes=30)-now).total_seconds())) if info and info['open'] else 0


def label(now=None):
    info=day_info(now)
    if info is None:
        return '당일 거래시간 확인 불가 · 매매 시간 확인 필요'
    if not info['open']:
        return '공식 거래일정: 휴장'
    return f"공식 정규장 {info['start']:%H:%M}~{info['end']:%H:%M}" + (' · 시간 변경일' if info['changed'] else '')


def main():
    import sys
    from .app import load_env
    load_env();refresh()
    if '--close-wait-seconds' in sys.argv:
        print(min(7200,close_wait_seconds()))
    else:
        print(label())


if __name__=='__main__':
    main()
