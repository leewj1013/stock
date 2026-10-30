from datetime import datetime
from stock_alarm.investment_timeline import compare, save, dashboard_data
from stock_alarm import investment_timeline as timeline
from unittest.mock import patch
import json


def fact(state, text):
    return {'label': '계좌 위험', 'state': state, 'text': text}


def test_missing_or_unknown_data_never_means_risk_resolved():
    old = {'risk:A': fact('warning', '중단')}
    assert compare(old, {})[0][0] == '확인 불가'
    assert compare(old, {'risk:A': fact('unknown', '조회 실패')})[0][0] == '확인 불가'
    assert compare(old, {'risk:A': fact('normal', '정상')})[0][0] == '위험 해소'


def test_prior_day_comparison_is_stable_across_repeated_dashboard_reads(tmp_path):
    path = str(tmp_path / 'timeline.db')
    save({'risk:A': fact('normal', '정상')}, path, datetime(2026, 10, 29, 16))
    save({'risk:A': fact('warning', '중단')}, path, datetime(2026, 10, 30, 10))
    first = dashboard_data(path, datetime(2026, 10, 30, 11))
    assert first['rows'][0][0] == '새 위험'
    assert first == dashboard_data(path, datetime(2026, 10, 30, 12))


def test_first_day_has_no_invented_yesterday_and_repeated_states_are_deduplicated(tmp_path):
    path = str(tmp_path / 'timeline.db')
    facts = {'risk:A': fact('normal', '정상')}
    save(facts, path, datetime(2026, 10, 30, 9))
    assert not save(facts, path, datetime(2026, 10, 30, 10))['added']
    assert not dashboard_data(path, datetime(2026, 10, 30, 11))['rows']
    assert '전일 기록 없음' in dashboard_data(path, datetime(2026, 10, 30, 11))['note']


def test_expired_last_observation_is_explicitly_labelled(tmp_path):
    path = str(tmp_path / 'timeline.db')
    save({'risk:A': fact('warning', '중단')}, path, datetime(2026, 10, 29, 16))
    assert '오늘 수집 전' in dashboard_data(path, datetime(2026, 10, 30))['note']


def test_removed_holdings_are_retired_only_when_account_source_is_available(tmp_path):
    for healthy in (True, False):
        path = str(tmp_path / f'{healthy}.db')
        save({'price:aggressive:A': fact('warning', '가격 확인 필요')}, path, datetime(2026, 10, 29, 16))
        original = timeline.query_rows
        def query(sql, parameters=(), path=path):
            if path == 'account':
                return [{'cash':100}] if healthy and 'virtual_accounts' in sql else []
            return original(sql, parameters, path=path)
        with patch.object(timeline,'query_rows',side_effect=query), \
             patch('stock_alarm.investment_insights.saved_account',return_value=({'holdings':[]}, [], [], [], {})), \
             patch('stock_alarm.investment_calendar.calendar',return_value={'rows':[],'notices':[],'checks':[]}), \
             patch('stock_alarm.stock_events.read_cache',return_value={'stocks':{}}):
            timeline.collect({'aggressive':{'db_path':'account'}},path,datetime(2026,10,30,10))
        rows = dashboard_data(path,datetime(2026,10,30,11))['rows']
        row = next(r for r in rows if r[1]=='계좌 위험')
        assert row[0] == ('추적 종료' if healthy else '확인 불가')
