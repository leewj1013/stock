"""Pre-open display marks only: no trades, risk snapshots, or research writes."""

from __future__ import annotations

import json
import math
import sqlite3
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
    price = float(row.get('lastPrice') or 0)
    if not math.isfinite(price) or price <= 0 or row.get('currency') != 'KRW':
        raise ValueError('invalid_price_or_currency')
    raw_stamp = str(row.get('timestamp') or '')
    stamp = datetime.fromisoformat(raw_stamp)
    if stamp.tzinfo is None:
        raise ValueError('missing_price_timezone')
    previous = now.date() - timedelta(days=1)
    while previous.weekday() >= 5 or previous in KR_MARKET_HOLIDAYS:
        previous -= timedelta(days=1)
    if stamp > now or stamp.astimezone(KST).date() not in {now.date(), previous}:
        raise ValueError('stale_or_future_price')
    return {'ticker': str(row['symbol']), 'price': price, 'price_at': stamp.astimezone(KST).isoformat(),
            'observed_at': now.isoformat(timespec='microseconds'),
            'venue': 'UNSPECIFIED', 'source': 'Toss 현재가', 'payload': row}


def refresh(now=None, profiles=None, client=None, store=STORE):
    now = (now or datetime.now(KST)).astimezone(KST)
    if not in_refresh_window(now):
        return {'state': 'skipped', 'reason': 'outside_preopen_window'}
    profiles = PROFILES if profiles is None else profiles
    holdings = {str(Path(p['db_path']).resolve()): virtual_trader_state(path=p['db_path'])['holdings']
                for p in profiles.values() if Path(p['db_path']).is_file()}
    tickers = sorted({h['ticker'] for rows in holdings.values() for h in rows})
    marks, failed = {}, {}
    if tickers:
        try:
            if client is None:
                from .toss_client import shared_client
                client = shared_client()
            rows = client.prices(tickers)
            for ticker in tickers:
                matches = [row for row in rows if row.get('symbol') == ticker]
                try:
                    if len(matches) != 1:
                        raise ValueError('missing_or_duplicate_symbol')
                    marks[ticker] = validate_mark(matches[0], now)
                except (ValueError, TypeError, KeyError, OverflowError):
                    failed[ticker] = 'invalid_or_missing_price'
        except Exception as error:
            failed = dict.fromkeys(tickers, type(error).__name__)
    Path(store).parent.mkdir(parents=True, exist_ok=True)
    statuses = []
    with closing(sqlite3.connect(store)) as db, db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS display_marks(
                ticker TEXT, observed_at TEXT, price_at TEXT, price REAL,
                venue TEXT, source TEXT, payload TEXT,
                PRIMARY KEY(ticker,observed_at));
            CREATE TABLE IF NOT EXISTS refresh_status(
                profile_path TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        for mark in marks.values():
            db.execute('INSERT OR IGNORE INTO display_marks VALUES(?,?,?,?,?,?,?)',
                       (mark['ticker'], mark['observed_at'], mark['price_at'], mark['price'],
                        mark['venue'], mark['source'], json.dumps(mark['payload'])))
        for path, rows in holdings.items():
            required = {h['ticker'] for h in rows}
            good = sorted({h['ticker'] for h in rows if h['ticker'] in marks
                           and price_after_entry(marks[h['ticker']]['price_at'], h.get('first_entry_at'))})
            missing = sorted(required - set(good))
            failed.update(dict.fromkeys(missing, 'missing_post_entry_price'))
            stamps = [marks[t]['price_at'] for t in good]
            status = {'attempted_at': now.isoformat(timespec='seconds'),
                      'state': 'partial' if missing and good else 'failed' if missing else 'completed',
                      'total': len(required), 'succeeded': len(good), 'failed_tickers': missing,
                      'price_at_min': min(stamps, key=observation_time) if stamps else '',
                      'price_at_max': max(stamps, key=observation_time) if stamps else '',
                      'venue': 'UNSPECIFIED'}
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


def refresh_label(status):
    if not status:
        return '장전 조회 대기'
    if status['state'] == 'unavailable':
        return '장전 저장 기록 확인 불가 · 기존 가격 기록 사용'
    state = {'completed': '정상', 'partial': '일부 실패', 'failed': '실패'}.get(status['state'], '확인 필요')
    label = f"장전 조회 {state} {status['succeeded']}/{status['total']} · 조회 {status['attempted_at'][:16].replace('T', ' ')}"
    if status.get('price_at_min'):
        label += f" · 시세 기준 {status['price_at_min'][:16].replace('T', ' ')} ~ {status['price_at_max'][:16].replace('T', ' ')} · 거래시장 미제공"
    if status['failed_tickers']:
        label += ' · 마지막 유효가격 유지: ' + ', '.join(status['failed_tickers'])
    return label


def main():
    from .app import load_env
    load_env()
    result = refresh()
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result.get('failed_tickers') else 0


if __name__ == '__main__':
    raise SystemExit(main())
