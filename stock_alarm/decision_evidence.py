"""Append-only decision facts, written in the same transaction as their source."""
import hashlib
import json
from datetime import datetime
from contextlib import closing


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS decision_evidence(
        evidence_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,source_key TEXT NOT NULL,
        ticker TEXT NOT NULL,occurred_at TEXT NOT NULL,captured_at TEXT NOT NULL,
        origin TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,
        UNIQUE(kind,source_key,ticker));
        CREATE INDEX IF NOT EXISTS idx_evidence_time ON decision_evidence(occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_evidence_kind_time ON decision_evidence(kind,occurred_at DESC);
        CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON decision_evidence
        BEGIN SELECT RAISE(ABORT,'Decision evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON decision_evidence
        BEGIN SELECT RAISE(ABORT,'Decision evidence is immutable'); END;''')


def append(db, kind, source_key, ticker, occurred_at, payload, origin='live'):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    db.execute('''INSERT OR IGNORE INTO decision_evidence
        (kind,source_key,ticker,occurred_at,captured_at,origin,payload,digest) VALUES(?,?,?,?,?,?,?,?)''',
        (kind, str(source_key), ticker, occurred_at, datetime.now().isoformat(timespec='seconds'),
         origin, text, hashlib.sha256(text.encode()).hexdigest()))


def run_context(db, run_id):
    row = db.execute('SELECT * FROM strategy_runs WHERE run_id=?', (run_id,)).fetchone()
    return dict(row) if row else {'run_id': run_id, 'note': '연결 실행 기록 없음'}


def recommendations(db, run_id):
    rows = db.execute('''SELECT c.*,p.profile,p.profile_score FROM candidate_snapshots c
        JOIN profile_candidate_selections p USING(run_id,ticker)
        WHERE c.run_id=? AND p.selected=1 AND p.profile IN ('aggressive','neutral')''', (run_id,)).fetchall()
    if not rows:
        rows = db.execute('SELECT *,\'aggressive\' profile FROM candidate_snapshots WHERE run_id=? AND selected=1', (run_id,)).fetchall()
    context = run_context(db, run_id)
    from .trading_profiles import PROFILES
    for row in rows:
        candidate = dict(row)
        profile = candidate.pop('profile')
        append(db, 'recommendation', run_id + ':' + profile, row['ticker'], row['evaluated_at'],
               {'candidate': candidate, 'run': context, 'profile': profile,
                'profile_rules': {k: v for k, v in PROFILES.get(profile, {}).items()
                                  if k not in ('db_path', 'sell_alerts_log', 'notify')}})


def sell_checks(db, run_id):
    context = run_context(db, run_id)
    for row in db.execute("SELECT * FROM position_checks WHERE run_id=? AND decision='SELL'", (run_id,)):
        append(db, 'sell_signal', run_id + ':' + row['position_id'], row['ticker'], row['checked_at'],
               {'check': dict(row), 'run': context})


def rule_summary(rules):
    if rules in (None, '', '기록 없음'):
        return '당시 규칙 미기록'
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except ValueError:
            return '당시 규칙 원문 확인 필요'
    if not isinstance(rules, dict):
        return '당시 규칙 미기록'
    values = {**(rules.get('environment') or {}), **rules}
    labels = {'TAKE_PROFIT_1_PCT': '1차 익절', 'TAKE_PROFIT_2_PCT': '2차 익절',
              'SELL_LOSS_PCT': '손절', 'MIN_RECOMMEND_SCORE': '추천 최소점수',
              'max_position_pct': '종목 최대 비중', 'min_fill_ratio': '최소 체결 비율',
              'max_holdings': '최대 보유종목'}
    result = [f'{label} {values[key]}' for key, label in labels.items() if values.get(key) not in (None, '')]
    policy = (rules.get('decision_context') or {}).get('sell_policy') or rules.get('sell_policy') or {}
    for key, label in (('stop_loss_pct','성향 손절'),('take_profit_1_pct','성향 1차 익절'),('take_profit_2_pct','성향 2차 익절')):
        if policy.get(key) is not None:
            result.append(f'{label} {policy[key]}%')
    return ' · '.join(result) or '규칙 전체 기록에서 확인' if rules else '당시 규칙 미기록'


def dashboard_data(path, limit=5, profile=None, recommendation_path=None):
    from .data_store import query_rows
    rows = []
    for kind in ('recommendation', 'buy', 'sell', 'sell_signal'):
        if kind == 'recommendation' and profile:
            rows += query_rows('SELECT * FROM decision_evidence WHERE kind=? AND source_key LIKE ? ORDER BY occurred_at DESC,evidence_id DESC LIMIT ?',
                               (kind, '%:'+profile, limit), path=recommendation_path or path)
        else:
            rows += query_rows('SELECT * FROM decision_evidence WHERE kind=? ORDER BY occurred_at DESC,evidence_id DESC LIMIT ?', (kind, limit), path=path)
    rows.sort(key=lambda r: (r['occurred_at'], r['evidence_id']), reverse=True)
    result, records = [], []
    labels = {'recommendation': '추천 판단', 'buy': '가상매수 체결', 'sell': '가상매도 체결', 'sell_signal': '매도 판단'}
    for row in rows:
        try:
            payload = json.loads(row['payload'])
            verified = hashlib.sha256(row['payload'].encode()).hexdigest() == row['digest']
            item = payload.get('execution') or payload.get('candidate') or payload.get('check') or {}
            source = payload.get('input') or item
            rules = payload.get('rules') or payload.get('profile_rules') or payload.get('run', {}).get('settings_json') or '기록 없음'
            score = next((v for v in (source.get('score'), item.get('profile_score'), item.get('final_score')) if v is not None), '미기록')
            origin = '당시 저장' if row['origin'] == 'live' else '기존 기록 복원'
            reason = source.get('reason') or source.get('reasons') or item.get('reasons') or ('성향별 추천 선정' if row['kind']=='recommendation' else '매수 입력·배정비중 보존' if row['kind']=='buy' else '사유 미기록')
            status = '정상' if verified else '무결성 확인 필요'
            run_id = payload.get('run', {}).get('run_id')
            if run_id:
                run_path = (recommendation_path or path) if row['kind']=='recommendation' else path
                run = query_rows('SELECT status FROM strategy_runs WHERE run_id=?', (run_id,), path=run_path)
                if run and run[0]['status'] != 'completed':
                    status += ' · 실행 '+{'running':'진행 중','failed':'실패'}.get(run[0]['status'], '상태 확인 필요')
            result.append([row['occurred_at'].replace('T', ' ')[:19], labels.get(row['kind'], row['kind']),
                           item.get('name') or row['ticker'], row['ticker'],
                           str(item.get('price') or item.get('close') or '기록 없음'),
                           str(score), reason, rule_summary(rules), origin, status])
            records.append({'title': f"{row['occurred_at']} · {labels.get(row['kind'])} · {item.get('name') or row['ticker']}",
                            'payload': json.dumps({'origin': origin, 'occurred_at': row['occurred_at'],
                                                  'captured_at': row['captured_at'], 'source_key': row['source_key'],
                                                  'digest': row['digest'], 'facts': payload}, ensure_ascii=False, indent=2)})
        except (ValueError, TypeError):
            continue
    return {'rows': result, 'records': records, 'note': f'유형별 최근 {limit}건. 당시 저장과 기존 기록 복원을 구분합니다. 과거 미기록 규칙은 현재 설정으로 소급 채우지 않습니다. 전체 기록에서 원래 입력·가격·지표·실행 설정과 해시를 확인할 수 있습니다.'}


def backfill(path, limit=300):
    """Archive existing saved facts, without inventing historical inputs or policy."""
    from .data_store import connect
    with closing(connect(path)) as db:
        before = db.execute('SELECT COUNT(*) FROM decision_evidence').fetchone()[0]
        for kind, table, id_field in (('buy','virtual_trades','trade_id'), ('sell','virtual_sales','sale_id')):
            for row in db.execute(f'SELECT * FROM {table} ORDER BY {id_field} DESC LIMIT ?', (limit,)).fetchall():
                append(db, kind, row[id_field], row['ticker'], row['created_at'],
                       {'execution': dict(row), 'note': '기존 체결 원장만 복원 · 당시 추가 입력·규칙 미기록'}, origin='restored')
        runs = db.execute('''SELECT r.run_id FROM strategy_runs r WHERE r.run_type='recommendation' AND (
            EXISTS(SELECT 1 FROM profile_candidate_selections p WHERE p.run_id=r.run_id
                   AND p.selected=1 AND p.profile IN ('aggressive','neutral')) OR
            EXISTS(SELECT 1 FROM candidate_snapshots c WHERE c.run_id=r.run_id AND c.selected=1))
            ORDER BY r.started_at DESC LIMIT 30''').fetchall()
        for run in runs:
            selected = db.execute('''SELECT c.*,p.profile,p.profile_score FROM candidate_snapshots c
                JOIN profile_candidate_selections p USING(run_id,ticker) WHERE c.run_id=?
                AND p.selected=1 AND p.profile IN ('aggressive','neutral')''', (run['run_id'],)).fetchall()
            if not selected:
                selected = db.execute("SELECT *,'aggressive' profile FROM candidate_snapshots WHERE run_id=? AND selected=1", (run['run_id'],)).fetchall()
            for row in selected:
                append(db, 'recommendation', run['run_id']+':'+row['profile'], row['ticker'], row['evaluated_at'],
                       {'candidate': dict(row), 'run': run_context(db, run['run_id']),
                        'note': '기존 추천·실행 기록만 복원 · 미기록 성향 규칙 추정 안 함'}, origin='restored')
        db.commit()
        return db.execute('SELECT COUNT(*) FROM decision_evidence').fetchone()[0] - before


def main():
    import sys
    from .app import load_env
    from .trading_profiles import PROFILES
    load_env()
    if '--backfill' not in sys.argv:
        raise SystemExit('Use --backfill to archive existing saved facts')
    print(json.dumps({key: backfill(PROFILES[key]['db_path']) for key in ('aggressive','neutral')}))


if __name__ == '__main__':
    main()
