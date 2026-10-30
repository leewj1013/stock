"""Saved state changes; dashboard reads never advance the comparison baseline."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .data_store import query_rows

PATH = 'data/investment_timeline.db'


def compare(before, after):
    rows = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if not new:
            # Missing source data is never evidence that a risk was resolved.
            kind, label, value = '확인 불가', old['label'], '현재 확인 자료 없음'
        else:
            label, value = new['label'], new['text']
            if new['state'] == 'retired':
                kind = '추적 종료'
            elif new['state'] == 'unknown':
                kind = '확인 불가'
            elif old and old['state'] == 'warning' and new['state'] == 'normal':
                kind = '위험 해소'
            elif new['state'] == 'warning' and (not old or old['state'] != 'warning'):
                kind = '새 위험'
            elif key.startswith('event:'):
                kind = '일정·공시 변경' if old else '새 일정·공시'
            elif not old:
                kind = '새 관찰 대상'
            else:
                kind = '상태 변경'
        rows.append([kind, label, old['text'] if old else '이전 기록 없음', value])
    return rows


def save(facts, path=PATH, now=None):
    now = now or datetime.now()
    payload = json.dumps(facts, ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS snapshots(
            snapshot_id INTEGER PRIMARY KEY,observed_at TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        db.execute('BEGIN IMMEDIATE')
        last = db.execute('SELECT observed_at,digest FROM snapshots ORDER BY snapshot_id DESC LIMIT 1').fetchone()
        stamp = now.isoformat(timespec='seconds')
        if last and stamp < last[0]:
            raise ValueError('A state snapshot cannot move backwards in time')
        added = not last or last[1] != digest or last[0][:10] != stamp[:10]
        if added:
            db.execute('INSERT INTO snapshots(observed_at,payload,digest) VALUES(?,?,?)', (stamp, payload, digest))
        db.execute("INSERT OR REPLACE INTO metadata VALUES('last_checked_at',?)", (stamp,))
        db.commit()
    return {'added': bool(added), 'observed_at': stamp, 'facts': len(facts)}


def dashboard_data(path=PATH, now=None):
    now = now or datetime.now()
    today = now.date().isoformat()
    latest = query_rows('SELECT * FROM snapshots WHERE observed_at<=? ORDER BY snapshot_id DESC LIMIT 1',
                        (now.isoformat(timespec='seconds'),), path=path)
    if not latest:
        return {'rows': [], 'note': '비교 기준 저장 전입니다. 첫 수집 이후부터 변경을 기록합니다.'}
    current = latest[0]
    previous = query_rows('SELECT * FROM snapshots WHERE observed_at<? ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1', (today,), path=path)
    basis = '전일 이전 마지막 저장 대비'
    if not previous:
        previous = query_rows('SELECT * FROM snapshots WHERE snapshot_id<? ORDER BY snapshot_id LIMIT 1', (current['snapshot_id'],), path=path)
        basis = '전일 기록 없음 · 첫 저장 대비'
    checked = query_rows("SELECT value FROM metadata WHERE key='last_checked_at'", path=path)
    note = basis + (' ' + previous[0]['observed_at'] if previous else ' · 오늘 기준 상태부터 보존')
    note += ' · 마지막 점검 ' + (checked[0]['value'] if checked else current['observed_at'])
    if current['observed_at'][:10] < today:
        note += ' · 오늘 수집 전: 아래는 마지막 저장 상태이며 오늘 발생으로 확정할 수 없습니다.'
    try:
        rows = compare(json.loads(previous[0]['payload']), json.loads(current['payload'])) if previous else []
    except (ValueError, KeyError, TypeError):
        return {'rows': [], 'note': '저장 상태를 읽지 못했습니다. 변경·위험 해소 여부를 확인할 수 없습니다.'}
    return {'rows': rows, 'note': note, 'empty': '비교 기준 이후 확인된 변경이 없습니다.'}


def collect(profiles=None, path=PATH, now=None):
    from .investment_insights import saved_account
    from .investment_calendar import calendar
    from .stock_events import read_cache
    from .trading_profiles import PROFILES
    profiles = profiles or {k: PROFILES[k] for k in ('aggressive', 'neutral')}
    now = now or datetime.now()
    today = now.date().isoformat()
    facts, held, available = {}, {}, {}
    labels = {'aggressive': '적극투자형', 'neutral': '위험중립형'}
    for key, profile in profiles.items():
        source = profile['db_path']
        name = labels.get(key, key)
        available[key] = bool(query_rows('SELECT cash FROM virtual_accounts WHERE account_id=1', path=source))
        if not available[key]:
            facts['risk:'+key] = {'label': name+' 위험관리', 'state': 'unknown', 'text': '계좌 자료 확인 불가'}
            continue
        state = saved_account(source)[0]
        held[key] = {h['ticker'] for h in state['holdings']}
        risk = query_rows('SELECT * FROM portfolio_risk_snapshots ORDER BY snapshot_id DESC LIMIT 1', path=source)
        status = risk[0]['status'] if risk else ''
        text = {'active': '정상 운영', 'reduced': '축소 운영', 'halted': '신규매수 중단'}.get(status, '위험관리 기록 확인 불가')
        facts['risk:'+key] = {'label': name+' 위험관리', 'state': 'warning' if status in ('reduced','halted') else 'normal' if status=='active' else 'unknown', 'text': text}
        for h in state['holdings']:
            facts[f'price:{key}:{h["ticker"]}'] = {'label': name+' · '+h['name'],
                'state': 'unknown' if h.get('current_price') is None else 'warning' if h['price_warning'] else 'normal',
                'text': '가격 기록 없음' if h.get('current_price') is None else
                        ('가격 확인 필요 · ' if h['price_warning'] else '가격 확인 · ') + h['price_basis_kind']}
        events = calendar(state['holdings'], now=now)
        for row in events['checks']:
            ticker = next((h['ticker'] for h in state['holdings'] if (h.get('name') or h['ticker'])==row[0]), None)
            if ticker:
                facts[f'collection:{key}:{ticker}'] = {'label': name+' · '+row[0]+' 일정 수집',
                    'state': 'normal' if row[2]=='수집 완료' else 'warning' if '실패' in row[2] else 'unknown',
                    'text': row[2]}
        for row in events['rows'] + events['notices']:
            ticker = next((h['ticker'] for h in state['holdings'] if (h.get('name') or h['ticker'])==row[0]), None)
            if ticker:
                event_state = row[4].split(' · 공시일 ')[0]
                facts[f'event:{ticker}:{row[1]}'] = {'label': row[0]+' · '+row[1],
                    'state': 'warning' if '취소·정정' in event_state else 'unknown' if '실패' in event_state else 'info',
                    'text': row[2]+' · '+row[3].strip()+' · '+event_state}
    all_held = set().union(*held.values()) if held else set()
    for ticker, item in read_cache().get('stocks', {}).items():
        if ticker not in all_held:
            continue
        info = item.get('info') or {}
        suspended = (info.get('koreanMarketDetail') or {}).get('krxTradingSuspended')
        reasons = item.get('review_reasons', [])
        fresh = item.get('checked_at', '')[:10] == today and not item.get('lookup_error')
        warning = bool(reasons or suspended is True or info.get('status') not in ('ACTIVE', None))
        facts['stock:'+ticker] = {'label': (info.get('name') or ticker)+' 거래 상태',
            'state': 'warning' if warning else 'normal' if fresh and suspended is False else 'unknown',
            'text': '; '.join(reasons) if reasons else '거래정지·상장 상태 확인 필요' if warning else '정상 거래 상태' if fresh and suspended is False else '거래 상태 최신 확인 불가'}
    previous = query_rows('SELECT payload FROM snapshots ORDER BY snapshot_id DESC LIMIT 1', path=path)
    old = json.loads(previous[0]['payload']) if previous else {}
    for key, value in old.items():
        if key in facts:
            continue
        parts = key.split(':')
        retired = (parts[0] in ('price','collection') and available.get(parts[1]) and parts[2] not in held.get(parts[1], set())
                   or parts[0] in ('event','stock') and all(available.values()) and parts[1] not in all_held)
        facts[key] = {**value, 'state': 'retired' if retired else 'unknown',
                      'text': '보유종목에서 제외 · 추적 종료' if retired else '현재 자료 확인 불가 · 해소 여부 미확인'}
    return save(facts, path, now)


def main():
    from .app import load_env
    load_env()
    print(json.dumps(collect()))


if __name__ == '__main__':
    main()
