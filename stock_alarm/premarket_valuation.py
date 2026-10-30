"""Pre-open display marks only: no trades, risk snapshots, or research writes."""

from __future__ import annotations

import json
import math
import sqlite3
import time as timer
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .account_valuation import observation_time, price_after_entry
from .data_store import virtual_trader_state
from .run_gate import KR_MARKET_HOLIDAYS
from .trading_profiles import PROFILES

STORE = 'data/dashboard_premarket.db'
KST = timezone(timedelta(hours=9))


def in_refresh_window(now):
    return (now.weekday() < 5 and now.date() not in KR_MARKET_HOLIDAYS
            and time(8) <= now.time().replace(tzinfo=None) < time(8, 50))


def validate_mark(row, now):
    try:
        price = float(row.get('lastPrice') or 0)
    except (TypeError, ValueError):
        raise ValueError('unparseable_price') from None
    if not math.isfinite(price) or price <= 0:
        raise ValueError('invalid_price')
    if row.get('currency') != 'KRW':
        raise ValueError('wrong_currency')
    raw_stamp = str(row.get('timestamp') or '')
    if not raw_stamp:
        raise ValueError('missing_timestamp')
    try:
        stamp = datetime.fromisoformat(raw_stamp)
    except ValueError:
        raise ValueError('invalid_timestamp') from None
    if stamp.tzinfo is None:
        raise ValueError('missing_price_timezone')
    previous = now.date() - timedelta(days=1)
    while previous.weekday() >= 5 or previous in KR_MARKET_HOLIDAYS:
        previous -= timedelta(days=1)
    if stamp > now:
        raise ValueError('future_price')
    if stamp.astimezone(KST).date() not in {now.date(), previous}:
        raise ValueError('stale_price')
    return {'ticker': str(row['symbol']), 'price': price, 'price_at': stamp.astimezone(KST).isoformat(),
            'observed_at': now.isoformat(timespec='microseconds'),
            'venue': 'UNSPECIFIED', 'source': 'Toss 현재가', 'payload': row}


def connect_store(store):
    Path(store).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(store)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS display_marks(
            ticker TEXT, observed_at TEXT, price_at TEXT, price REAL,
            venue TEXT, source TEXT, payload TEXT,
            PRIMARY KEY(ticker,observed_at));
        CREATE TABLE IF NOT EXISTS refresh_status(
            profile_path TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS refresh_attempts(
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL, payload TEXT NOT NULL);
    ''')
    return db


def refresh(now=None, profiles=None, client=None, store=STORE, max_attempts=3, retry_delay=2, sleep_fn=timer.sleep):
    clock = (lambda: now.astimezone(KST)) if now is not None else lambda: datetime.now(KST)
    now = clock()
    if not in_refresh_window(now):
        return {'state': 'skipped', 'reason': 'outside_preopen_window'}
    profiles = PROFILES if profiles is None else profiles
    holdings = {str(Path(p['db_path']).resolve()): virtual_trader_state(path=p['db_path'])['holdings']
                for p in profiles.values() if Path(p['db_path']).is_file()}
    tickers = sorted({h['ticker'] for rows in holdings.values() for h in rows})
    with closing(connect_store(store)) as db, db:
        for path, rows in holdings.items():
            db.execute('INSERT OR REPLACE INTO refresh_status VALUES(?,?)',
                (path, json.dumps({'state':'running', 'attempted_at':now.isoformat(timespec='seconds'),
                                   'succeeded':0, 'total':len({h['ticker'] for h in rows}), 'failed_tickers':[]})))
    marks, failed, attempts = {}, {}, []
    if tickers:
        for attempt in range(1, max(1, min(3, max_attempts)) + 1):
            pending = sorted(set(tickers) - marks.keys())
            if not pending or not in_refresh_window(clock()):
                break
            terminal = False
            try:
                if client is None:
                    from .toss_client import shared_client
                    client = shared_client()
                rows = client.prices(pending)
                observed = clock()
                if not in_refresh_window(observed):
                    raise ValueError('window_ended')
                if not isinstance(rows, list):
                    raise ValueError('invalid_response')
                for ticker in pending:
                    matches = [r for r in rows if isinstance(r, dict) and r.get('symbol') == ticker]
                    detail = {'attempt': attempt, 'ticker': ticker, 'at': observed.isoformat()}
                    try:
                        if len(matches) != 1:
                            raise ValueError('missing_symbol' if not matches else 'duplicate_symbol')
                        detail['response'] = {k: matches[0].get(k) for k in ('symbol','timestamp','lastPrice','currency')}
                        marks[ticker] = validate_mark(matches[0], observed)
                        failed.pop(ticker, None)
                        detail['reason'] = 'ok'
                    except (ValueError, TypeError, KeyError, OverflowError) as error:
                        detail['reason'] = str(error) if isinstance(error, ValueError) else 'invalid_response'
                        failed[ticker] = detail['reason']
                    attempts.append(detail)
            except Exception as error:
                status = getattr(error, 'status', None)
                reason = 'window_ended' if isinstance(error, ValueError) and str(error) == 'window_ended' else 'api_error'
                terminal = status in (401, 403) or isinstance(error, ValueError)
                for ticker in pending:
                    failed[ticker] = reason
                    attempts.append({'attempt': attempt, 'ticker': ticker, 'at': clock().isoformat(),
                                     'reason': reason, 'error_type': type(error).__name__,
                                     'http_status': status if isinstance(status, int) else None})
            if terminal or len(marks) == len(tickers) or attempt == min(3, max_attempts):
                break
            sleep_fn(max(0, min(5, retry_delay)))
    statuses = []
    with closing(connect_store(store)) as db, db:
        for detail in attempts:
            db.execute('INSERT INTO refresh_attempts(run_at,payload) VALUES(?,?)',
                       (now.isoformat(), json.dumps(detail)))
        for mark in marks.values():
            db.execute('INSERT OR IGNORE INTO display_marks VALUES(?,?,?,?,?,?,?)',
                       (mark['ticker'], mark['observed_at'], mark['price_at'], mark['price'],
                        mark['venue'], mark['source'], json.dumps(mark['payload'])))
        for path, rows in holdings.items():
            required = {h['ticker'] for h in rows}
            good = sorted({h['ticker'] for h in rows if h['ticker'] in marks
                           and price_after_entry(marks[h['ticker']]['price_at'], h.get('first_entry_at'))})
            missing = sorted(required - set(good))
            for ticker in missing:
                failed.setdefault(ticker, 'missing_post_entry_price')
            stamps = [marks[t]['price_at'] for t in good]
            status = {'attempted_at': now.isoformat(timespec='seconds'),
                      'state': 'partial' if missing and good else 'failed' if missing else 'completed',
                      'total': len(required), 'succeeded': len(good), 'failed_tickers': missing,
                      'price_at_min': min(stamps, key=observation_time) if stamps else '',
                      'price_at_max': max(stamps, key=observation_time) if stamps else '',
                      'venue': 'UNSPECIFIED'}
            status['failure_reasons'] = {t: failed[t] for t in missing}
            status['attempts'] = {t: sum(a['ticker'] == t for a in attempts) for t in required}
            db.execute('INSERT OR REPLACE INTO refresh_status VALUES(?,?)', (path, json.dumps(status)))
            statuses.append(status)
    return {'state': 'partial' if failed else 'completed', 'tickers': len(tickers),
            'succeeded': len(marks), 'failed_tickers': sorted(failed), 'accounts': len(statuses)}


def read_premarket(profile_path, store=STORE):
    if not Path(store).is_file():
        return [], {}
    try:
        with closing(sqlite3.connect(Path(store).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('''SELECT * FROM display_marks m WHERE observed_at=(
                SELECT MAX(observed_at) FROM display_marks n WHERE n.ticker=m.ticker)''').fetchall()
            status = db.execute('SELECT payload FROM refresh_status WHERE profile_path=?',
                                (str(Path(profile_path).resolve()),)).fetchone()
    except sqlite3.DatabaseError:
        return [], {'state': 'unavailable'}
    return [dict(row) for row in rows], json.loads(status[0]) if status else {}


REASON_LABELS = {'missing_timestamp': '시세 시각 없음', 'invalid_timestamp': '시각 형식 오류',
    'missing_price_timezone': '시간대 없음', 'future_price': '미래 시각', 'stale_price': '오래된 시세',
    'wrong_currency': '통화 오류', 'invalid_price': '비정상 가격', 'unparseable_price': '가격 형식 오류',
    'missing_symbol': '응답 누락', 'duplicate_symbol': '중복 응답', 'invalid_response': '응답 형식 오류',
    'api_error': 'API 조회 실패', 'window_ended': '장전 조회 시간 종료',
    'missing_post_entry_price': '진입 이후 시세 없음'}


def refresh_check(status, now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    stamp = observation_time(status.get('attempted_at'))
    if status.get('state') == 'unavailable':
        run_state = 'unavailable'
    elif now.weekday() >= 5 or now.date() in KR_MARKET_HOLIDAYS:
        run_state = 'non_trading_day'
    elif stamp and stamp.astimezone(KST).date() == now.date():
        run_state = status.get('state', 'failed')
        if run_state == 'running' and (now - stamp).total_seconds() >= 300:
            run_state = 'delayed'
    elif now.time() < time(8, 30):
        run_state = 'scheduled'
    elif now.time() < time(8, 35):
        run_state = 'pending'
    else:
        run_state = 'missed'
    return {**status, 'run_state': run_state,
            'needs_attention': run_state in {'unavailable', 'missed', 'partial', 'failed', 'delayed'}}


def refresh_label(status, now=None):
    checked = refresh_check(status, now)
    if checked['run_state'] in {'scheduled', 'pending', 'missed', 'non_trading_day', 'running', 'delayed'}:
        label = {'scheduled': '장전 조회 예정 08:30', 'pending': '장전 조회 대기 · 08:35 실행 여부 확인',
                 'missed': '오늘 장전 조회 완료 기록 없음 · 예약 실행/지연 확인 필요',
                 'running': '장전 조회 진행 중', 'delayed': '장전 조회 5분 이상 지연 · 작업 확인 필요',
                 'non_trading_day': '비거래일 · 장전 조회 대상 아님'}[checked['run_state']]
        if status.get('attempted_at'):
            label += ' · 마지막 조회 ' + status['attempted_at'][:16].replace('T', ' ')
        return label
    if status['state'] == 'unavailable':
        return '장전 저장 기록 확인 불가 · 기존 가격 기록 사용'
    state = {'completed': '정상', 'partial': '일부 실패', 'failed': '실패'}.get(status['state'], '확인 필요')
    label = f"장전 조회 {state} {status['succeeded']}/{status['total']} · 조회 {status['attempted_at'][:16].replace('T', ' ')}"
    if status.get('price_at_min'):
        label += f" · 시세 기준 {status['price_at_min'][:16].replace('T', ' ')} ~ {status['price_at_max'][:16].replace('T', ' ')} · 거래시장 미제공"
    if status['failed_tickers']:
        details = []
        for ticker in status['failed_tickers']:
            reason = REASON_LABELS.get(status.get('failure_reasons', {}).get(ticker), '사유 미기록(보완 전 기록)')
            count = status.get('attempts', {}).get(ticker)
            details.append(f'{ticker} {reason}' + (f'/{count}회' if count else ''))
        label += ' · 마지막 유효가격 유지: ' + ', '.join(details)
    return label


def main():
    from .app import load_env
    load_env()
    result = refresh()
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result.get('failed_tickers') else 0


if __name__ == '__main__':
    raise SystemExit(main())
