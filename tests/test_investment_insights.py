from datetime import datetime
import json
import hashlib
import sqlite3
from unittest.mock import patch

from stock_alarm.investment_insights import attribution, concentration, execution_quality, related_positions
from stock_alarm.investment_calendar import collect, calendar, event_details
from stock_alarm.investment_market import market_breadth, recommendation_changes, research_coverage, historical_coverage
from stock_alarm.investment_dashboard import render_panel
from stock_alarm.investment_insights import saved_account


def test_attribution_reconciles_deposit_partial_sale_and_unrealized_change():
    state = {'total_equity': 1550, 'unrealized_profit_loss': 30}
    base = [{'created_at': '2026-10-29T15:40:00', 'equity': 1000, 'profit_loss': 10},
            {'created_at': '2026-10-30T09:30:00', 'equity': 1500, 'profit_loss': 10}]
    result = attribution(state, base, [{'created_at': '2026-10-30T08:00:00', 'amount': 500}],
                         [{'created_at': '2026-10-30T10:00:00', 'realized_profit_loss': 30}],
                         datetime(2026, 10, 30, 16))
    assert result['change'] == 550
    assert result['residual'] == 0
    assert '2026-10-29' in result['note']


def test_unknown_prices_do_not_become_zero_change_or_zero_risk():
    assert not attribution({'total_equity': None}, [], [], [])['available']
    assert concentration({'total_equity': None}, {})['rows'] == []


def test_concentration_uses_total_equity_and_retains_unknown_sector():
    state = {'total_equity': 1000, 'cash': 500, 'holdings': [
        {'ticker': 'A', 'valuation': 300}, {'ticker': 'B', 'valuation': 200}]}
    result = concentration(state, {'A': '반도체'})
    assert ['현금 비중', '50.0%'] in result['rows']
    assert ['업종 · 반도체', '30.0%'] in result['rows']
    assert ['업종 · 미분류', '20.0%'] in result['rows']


def test_execution_never_uses_future_signal_and_preserves_costs():
    trades = [{'ticker': 'A', 'created_at': '2026-10-30T10:00:00', 'source': 'recommendation',
               'quantity': 2, 'price': 110, 'cost': 222}]
    candidates = [{'ticker': 'A', 'evaluated_at': '2026-10-30T09:59:00', 'close': 100},
                  {'ticker': 'A', 'evaluated_at': '2026-10-30T10:01:00', 'close': 200}]
    row = execution_quality(trades, [], candidates, [])['rows'][0]
    assert row[3] == '100원'
    assert row[5] == '+10.000%'
    assert row[6] == '2원'


def test_future_and_manual_reference_do_not_become_slippage():
    trade = {'ticker': 'A', 'created_at': '2026-10-30T10:00:00', 'source': 'manual',
             'quantity': 2, 'price': 110, 'cost': 220}
    candidate = {'ticker': 'A', 'evaluated_at': '2026-10-30T09:59:00', 'close': 100}
    assert execution_quality([trade], [], [candidate], [])['rows'][0][5] == '비교 불가'


def test_calendar_disclosure_day_is_not_an_event_day_and_correction_clears_date(tmp_path):
    path = str(tmp_path / 'events.db')
    filings = [{'report_nm': '유상증자결정', 'rcept_no': '20261030000001', 'rcept_dt': '20261030'},
               {'report_nm': '[정정]결산실적공시예고', 'rcept_no': '20261030000002', 'rcept_dt': '20261030'}]
    collect(['A'], path, lambda *a, **kw: filings,
            lambda _: '결산실적 공시 예정일 2026-11-05', datetime(2026, 10, 30))
    result = calendar([{'ticker': 'A'}], path, datetime(2026, 10, 30))
    assert not result['rows']
    assert len(result['notices']) == 2
    assert all(row[2] == '효력일 미확인' for row in result['notices'])


def test_failed_calendar_lookup_keeps_saved_notices(tmp_path):
    path = str(tmp_path / 'events.db')
    filing = {'report_nm': '결산실적공시예고', 'rcept_no': '20261030000001', 'rcept_dt': '20261030'}
    collect(['A'], path, lambda *a, **kw: [filing],
            lambda _: '결산실적 공시 예정일 2026-11-05', datetime(2026, 10, 30))
    def fail(*a, **kw):
        raise OSError('network')
    collect(['A'], path, fail, now=datetime(2026, 10, 31))
    result = calendar([{'ticker': 'A'}], path, datetime(2026, 10, 31))
    assert result['rows'][0][2] == '2026-11-05'
    assert '조회 실패' in result['checks'][0][2]


def test_correlations_do_not_join_different_return_intervals():
    state = {'total_equity': 100, 'holdings': [
        {'ticker': 'A', 'valuation': 30, 'price_observed_at': '2026-10-30'},
        {'ticker': 'B', 'valuation': 30, 'price_observed_at': '2026-10-30'}]}
    def bars(ticker):
        return [{'date': f'202610{i:02d}', 'close': str(100 + i*i)} for i in range(1, 26)
                if ticker == 'A' or i != 13]
    assert not related_positions(state, bars)['rows']


def test_market_breadth_never_mixes_batches_or_counts_missing_as_flat():
    batch = [{'run_id': 'new', 'ticker': 'A', 'evaluated_at': '2026-10-30T10:00:00', 'day_return_pct': 2},
             {'run_id': 'new', 'ticker': 'B', 'evaluated_at': '2026-10-30T10:00:00', 'day_return_pct': None},
             {'run_id': 'old', 'ticker': 'C', 'evaluated_at': '2026-10-29T10:00:00', 'day_return_pct': -2}]
    with patch('stock_alarm.investment_market.query_rows', return_value=[]):
        result = market_breadth(batch, lambda _: [])
    assert ['수집된 평가 종목', '2종목'] in result['rows']
    assert ['상승 종목 비율', '100.0%'] in result['rows']
    assert '전체 KOSPI·KOSDAQ 통계가 아닙니다' in result['note']


def test_recommendation_score_and_price_changes_are_compared_to_recommendation():
    rows = [{'ticker': 'A', 'evaluated_at': '2026-10-29T10:00:00', 'selected': 1, 'close': 100, 'final_score': 70},
            {'ticker': 'A', 'evaluated_at': '2026-10-30T10:00:00', 'selected': 0, 'passed': 0, 'close': 95,
             'final_score': 60, 'rejection_reasons': '거래량 부족'}]
    row = recommendation_changes(rows)['rows'][0]
    assert row[4] == '-5.00%'
    assert row[5] == '70.0 → 60.0 (-10.0)'
    assert row[6] == '최근 평가 조건 미달'
    assert row[7] == '거래량 부족'


def test_research_uses_only_complete_error_free_eligible_trades():
    outcome = json.dumps({'arms': {'early': {'net_return_pct': 3}, 'baseline': {'net_return_pct': 1}}})
    cases = [{'complete': 1, 'error': None, 'outcomes': outcome, 'bought_at': '2026-10-30T09:00:00'},
             {'complete': 0, 'error': None, 'outcomes': outcome, 'bought_at': '2026-10-30T09:00:00'},
             {'complete': 1, 'error': 'bad', 'outcomes': outcome, 'bought_at': '2026-10-30T09:00:00'}]
    with patch('stock_alarm.investment_market.query_rows', return_value=cases):
        result = research_coverage(lambda _: [])
    assert result['rows'][0][1] == '1/3건'
    assert result['rows'][0][5] == '1건'
    assert result['rows'][1][1] == '0/3건'


def test_panel_escapes_saved_text_and_only_links_verified_dart_urls():
    html = render_panel({'title': '<script>', 'headers': ['내용'],
                         'rows': [['<img src=x onerror=1>'], ['javascript:alert(1)'],
                                  ['https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261030000001']]})
    assert '<script>' not in html and '<img ' not in html
    assert 'href="javascript:' not in html
    assert html.count('<a href=') == 1


def test_calendar_refreshes_again_after_open_to_close_interval(tmp_path):
    path = str(tmp_path / 'calendar.db')
    fetch = lambda *a, **kw: []
    collect(['A'], path, fetch, now=datetime(2026, 10, 30, 8, 50))
    result = collect(['A'], path, fetch, now=datetime(2026, 10, 30, 16))
    assert result['tickers'] == 1
    assert calendar([{'ticker': 'A'}], path, datetime(2026, 10, 30))['checks'][0][1].endswith('16:00:00')


def test_withdrawn_flag_on_same_receipt_invalidates_previously_known_date(tmp_path):
    path = str(tmp_path / 'calendar.db')
    filing = {'report_nm': '결산실적공시예고', 'rcept_no': '20261030000001', 'rcept_dt': '20261030'}
    document = lambda _: '결산실적 공시 예정일 2026-11-05'
    collect(['A'], path, lambda *a, **kw: [filing], document, datetime(2026, 10, 30, 8))
    assert calendar([{'ticker': 'A'}], path, datetime(2026, 10, 30))['rows']
    collect(['A'], path, lambda *a, **kw: [{**filing, 'rm': '철'}], document, datetime(2026, 10, 30, 16))
    result = calendar([{'ticker': 'A'}], path, datetime(2026, 10, 30))
    assert not result['rows']
    assert '취소·정정' in result['notices'][0][4]


def test_saved_account_reads_current_cycle_without_mutating_ledger(tmp_path):
    file = tmp_path / 'account.db'
    with sqlite3.connect(file) as db:
        db.executescript('''CREATE TABLE virtual_trades(trade_id,ticker,name,quantity,cost,created_at);
            INSERT INTO virtual_trades VALUES(1,'A','종목',2,200,'2026-10-30T10:00:00');
            INSERT INTO virtual_trades VALUES(2,'A','종목',1,200,'2026-10-30T10:00:00');
            CREATE TABLE virtual_sales(sale_id,ticker,quantity,cost_basis,sale_type,created_at);
            INSERT INTO virtual_sales VALUES(1,'A',2,200,'full','2026-10-30T10:00:00');
            CREATE TABLE virtual_accounts(account_id,cash); INSERT INTO virtual_accounts VALUES(1,800);
            CREATE TABLE virtual_deposits(amount); INSERT INTO virtual_deposits VALUES(1000);
            CREATE TABLE position_checks(ticker,checked_at,close);
            INSERT INTO position_checks VALUES('A','2026-10-30T11:00:00',210);
            CREATE TABLE price_quality(ticker,created_at,status,close);''')
    before = hashlib.sha256(file.read_bytes()).digest()
    with patch('stock_alarm.investment_insights.read_premarket', return_value=([], {})):
        state = saved_account(str(file))[0]
    assert state['holdings'][0]['quantity'] == 1
    assert state['holdings'][0]['cost'] == 200
    assert state['total_equity'] == 1010
    assert hashlib.sha256(file.read_bytes()).digest() == before


def test_budget_exhaustion_marks_deferred_tickers_instead_of_success(tmp_path):
    path = str(tmp_path / 'events.db')
    collect(['A'], path, lambda *a, **kw: [], budget_seconds=0)
    assert '시간 제한' in calendar([{'ticker': 'A'}], path)['checks'][0][2]


def test_historical_samples_are_labelled_separately_from_forward_samples(tmp_path):
    folder = tmp_path / 'current_data_experiment'
    folder.mkdir()
    (folder / 'results.json').write_text(json.dumps({'generated_at': '2026-10-29T15:00:00', 'included': 48}), encoding='utf-8')
    rows = historical_coverage(tmp_path)
    assert len(rows) == 1
    assert rows[0][1] == '48종료 거래 · 과거'
    assert rows[0][-1] == '과거 탐색 · 전향 검증과 별개'


def test_recommendation_rejection_codes_are_readable_korean():
    row = {'ticker': 'A', 'evaluated_at': '2026-10-30T10:00:00', 'selected': 1, 'close': 100,
           'rejection_reasons': 'volume_ratio,below_ma20,score_or_portfolio_filter'}
    text = recommendation_changes([row])['rows'][0][7]
    assert text == '거래량 기준 미달, 20일선 아래, 점수·보유 비중·종목 수 기준으로 미선정'
