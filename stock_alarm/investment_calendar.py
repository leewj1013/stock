"""Saved holding-event watchlist; disclosure dates never become effective dates."""
import json
import hashlib
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from .data_store import query_rows

PATH = 'data/investment_calendar.db'
KINDS = (('결산실적공시예고', '실적 발표'), ('기업설명회', '기업설명회'),
         ('배당', '배당'), ('유상증자', '유상증자'), ('주식분할', '주식분할'),
         ('주식병합', '주식병합'), ('감자', '감자'), ('합병', '합병'),
         ('회사분할', '회사분할'), ('거래정지', '거래정지'))


def classify(title):
    compact = re.sub(r'\s+', '', title)
    return next((kind for word, kind in KINDS if word in compact), '')


def event_details(filing, document=''):
    from .research_data_collect import announcement_details, ir_schedule_details, withdrawal_evidence
    kind = classify(filing.get('report_nm', ''))
    state, day = '효력일 미확인', None
    if kind == '실적 발표' and document:
        day = announcement_details(document)['expected_date']
        state = '예정일 확인' if day else state
    if kind == '기업설명회' and document:
        details = ir_schedule_details(document)
        day = details.get('date')
        state = '예정일 확인' if details['state'] == 'IR_SCHEDULED' else '취소·정정 확인 필요'
    if withdrawal_evidence(filing, document) or '정정' in filing.get('report_nm', ''):
        day, state = None, '취소·정정 확인 필요'
    return kind, day, state


def _filings(ticker, days=90, limit=100):
    from .dart_reference import corp_code_by_stock
    from .research_data_collect import dart_json
    code = corp_code_by_stock(ticker)
    if not code:
        raise ValueError('Corporation identifier unavailable')
    today = datetime.now().date()
    return dart_json({'corp_code': code, 'bgn_de': (today-timedelta(days=days)).strftime('%Y%m%d'),
                      'end_de': today.strftime('%Y%m%d'), 'last_reprt_at': 'N', 'page_count': limit}).get('list', [])


def collect(tickers, path=PATH, disclosures_fn=None, document_fn=None, now=None, budget_seconds=60):
    from .research_data_collect import dart_document
    now = now or datetime.now()
    disclosures_fn = disclosures_fn or _filings
    document_fn = document_fn or dart_document
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    added = failed = 0
    deadline = time.monotonic() + budget_seconds
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS events(
            ticker TEXT,receipt TEXT,kind TEXT,title TEXT,published_date TEXT,event_date TEXT,
            state TEXT,checked_at TEXT,url TEXT,PRIMARY KEY(ticker,receipt));
            CREATE TABLE IF NOT EXISTS status(ticker TEXT PRIMARY KEY,checked_at TEXT,state TEXT);''')
        if 'filing_hash' not in {r[1] for r in db.execute('PRAGMA table_info(events)')}:
            db.execute('ALTER TABLE events ADD COLUMN filing_hash TEXT')
        for ticker in sorted(set(tickers)):
            previous = db.execute('SELECT checked_at,state FROM status WHERE ticker=?', (ticker,)).fetchone()
            if previous and previous[1] == '수집 완료' and now - datetime.fromisoformat(previous[0]) < timedelta(hours=6):
                continue
            stamp = now.isoformat(timespec='seconds')
            if not os.environ.get('DART_API_KEY') and disclosures_fn is _filings:
                db.execute('INSERT OR REPLACE INTO status VALUES(?,?,?)', (ticker, stamp, 'DART 미연결'))
                db.commit()
                continue
            try:
                if time.monotonic() >= deadline:
                    db.execute('INSERT OR REPLACE INTO status VALUES(?,?,?)', (ticker, stamp, '일부 수집 · 시간 제한'))
                    db.commit()
                    continue
                filings = disclosures_fn(ticker, days=90, limit=100)
                partial = len(filings) >= 100
                for filing in filings:
                    if time.monotonic() >= deadline:
                        partial = True
                        break
                    title = filing.get('report_nm', '')
                    if not classify(title):
                        continue
                    receipt = str(filing.get('rcept_no') or '')
                    if not re.fullmatch(r'\d{14}', receipt):
                        continue
                    digest = hashlib.sha256(json.dumps(filing, sort_keys=True).encode()).hexdigest()
                    known = db.execute('SELECT state,filing_hash FROM events WHERE ticker=? AND receipt=?', (ticker, receipt)).fetchone()
                    if known and known[0] != '원문 조회 실패' and known[1] == digest:
                        continue
                    doc, doc_failed = '', False
                    if classify(title) in ('실적 발표', '기업설명회'):
                        try:
                            doc = document_fn(receipt)
                        except Exception:
                            doc_failed = True
                    kind, day, state = event_details(filing, doc)
                    if doc_failed:
                        state = '원문 조회 실패'
                    db.execute('INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (ticker, receipt, kind, title, filing.get('rcept_dt') or '', day, state, stamp,
                                'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt, digest))
                    added += 1
                db.execute('INSERT OR REPLACE INTO status VALUES(?,?,?)',
                           (ticker, stamp, '일부 수집 · 목록 상한/시간 제한' if partial else '수집 완료'))
                db.commit()
            except Exception:
                db.rollback()
                failed += 1
                db.execute('INSERT OR REPLACE INTO status VALUES(?,?,?)', (ticker, stamp, '조회 실패 · 이전 자료 유지'))
                db.commit()
    return {'added': added, 'failed': failed, 'tickers': len(set(tickers))}


def calendar(holdings, path=PATH, now=None):
    now = now or datetime.now()
    today, until = now.date().isoformat(), (now + timedelta(days=30)).date().isoformat()
    names = {h['ticker']: h.get('name') or h['ticker'] for h in holdings}
    all_events = query_rows('SELECT * FROM events ORDER BY published_date DESC,receipt DESC', path=path)
    statuses = {r['ticker']: r for r in query_rows('SELECT * FROM status', path=path)}
    # The newest notice of each kind controls the watchlist. Unresolved corrections
    # suppress an older scheduled date rather than presenting it as still valid.
    latest = {}
    for item in all_events:
        if item['ticker'] in names:
            latest.setdefault((item['ticker'], item['kind']), item)
    upcoming, notices = [], []
    for (ticker, kind), item in latest.items():
        row = [names[ticker], kind, item['event_date'] or '효력일 미확인', item['title'], item['state'], item['url']]
        if item['event_date'] and today <= item['event_date'] <= until and item['state'] == '예정일 확인':
            upcoming.append(row)
        else:
            notices.append([*row[:2], row[2], row[3], row[4] + ' · 공시일 ' + item['published_date'], row[5]])
    checks = [[name, statuses.get(ticker, {}).get('checked_at') or '기록 없음',
               statuses.get(ticker, {}).get('state', '수집 대기')
               + (' · 이전 날짜 자료' if statuses.get(ticker, {}).get('checked_at', '')[:10] < today else '')]
              for ticker, name in names.items()]
    return {'rows': sorted(upcoming, key=lambda r: r[2]), 'notices': notices, 'checks': checks,
            'note': '보유종목 · 향후 30일 · 최근 90일 공시 중 유형별 최신 안내. 공시일은 효력일이 아닙니다. 배당락·증자·분할·합병·거래정지는 원문 확인 전 날짜를 추정하지 않습니다. 목록에 없다고 일정이 없다는 뜻은 아닙니다.'}


def main():
    from .app import load_env
    from .investment_insights import saved_account
    from .trading_profiles import PROFILES
    load_env()
    tickers = {h['ticker'] for p in PROFILES.values() if Path(p['db_path']).is_file()
               for h in saved_account(p['db_path'])[0]['holdings']}
    print(json.dumps(collect(tickers)))


if __name__ == '__main__':
    main()

