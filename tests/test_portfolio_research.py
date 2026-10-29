import json
from contextlib import closing
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stock_alarm import buy_observer as observer, portfolio_research as research, data_store, app, portfolio_risk


def state():
    return {"cash": 40000, "holdings": [{"ticker": "OLD", "cost": 60000, "quantity": 600}]}


def candidate(ticker="NEW", allocation=25):
    return {"ticker": ticker, "name": ticker, "close": 100, "allocation_pct": allocation, "portfolio_limit_pct": 70, "score": 80}


def rows(prices, start=date(2026, 10, 1)):
    return [[(start + timedelta(days=i)).strftime('%Y%m%d'), o, max(o, c), min(o, c), c, 1000] for i, (o, c) in enumerate(prices)]


def test_budget_variants_change_one_constraint_at_a_time():
    c = [candidate()]
    base = observer.allocation_plan(c, state(), 30, .5)[0]
    small = observer.allocation_plan(c, state(), 30, .1)[0]
    cap = observer.allocation_plan(c, state(), 30, .5, 10)[0]
    assert base["reason"] == "minimum_fill" and base["binding_constraint"] == "portfolio_cap"
    assert base["cost"] == 0 and small["cost"] == 10000 and cap["cost"] == 20000


def test_budget_reconstruction_matches_actual_integer_orders(tmp_path, monkeypatch):
    path = str(tmp_path / "account.db")
    monkeypatch.setenv("VIRTUAL_TRADER_MAX_POSITION_PCT", "100")
    monkeypatch.setenv("VIRTUAL_TRADER_MIN_FILL_RATIO", "0.5")
    with patch.object(portfolio_risk, 'new_buys_allowed', return_value=(True, '')):
        data_store.virtual_deposit(100000, path=path)
        data_store.virtual_buy([candidate('OLD', 60)], path=path)
        before = data_store.virtual_trader_state(path=path)
        candidates = [candidate('A', 20), candidate('B', 15)]
        expected = observer.allocation_plan(candidates, before, 100, .5)
        actual = data_store.virtual_buy(candidates, path=path)['executions']
    assert {r['ticker']: r['quantity'] for r in expected if r['quantity']} == {r['ticker']: r['quantity'] for r in actual}
    assert expected[1]['reason'] == 'portfolio_cap'


def test_observer_preserves_result_and_restores_functions(tmp_path):
    path = str(tmp_path / 'observations.db')
    connect = observer.connect
    original_state = data_store.virtual_trader_state
    picks = [SimpleNamespace(ticker='NEW', name='new', close=100, score=80)]
    expected = {'executions': [{'ticker': 'NEW', 'quantity': 100}]}
    def original(picks, **kwargs):
        data_store.virtual_trader_state()
        portfolio_risk.new_buys_allowed()
        return data_store.virtual_buy([candidate()])
    with patch.object(observer, 'connect', side_effect=lambda: connect(path)), patch.object(data_store, 'query_rows', return_value=[]), \
         patch.object(data_store, 'virtual_trader_state', return_value=state()), patch.object(portfolio_risk, 'new_buys_allowed', return_value=(True, '')), \
         patch.object(data_store, 'virtual_buy', return_value=expected):
        assert observer.observe_auto_buy(original, picks) is expected
    assert data_store.virtual_trader_state is original_state
    with closing(connect(path)) as db:
        saved = json.loads(db.execute('SELECT payload FROM buy_attempts').fetchone()[0])
        assert saved['executions'] == expected['executions']
        assert 'take_profit_1_pct' in saved['entry_take_profit_policy']
        assert research.decisions(saved)[0]['reason'] == 'executed'


def test_observation_failure_does_not_cancel_trade(capsys):
    with patch.object(observer, 'connect', side_effect=OSError('disk failed')), patch.object(data_store, 'query_rows', return_value=[]), patch.object(app, 'write_error_log'):
        expected = {'executions': []}
        assert observer.observe_auto_buy(lambda picks: expected, []) is expected
    assert 'buy observation failed' in capsys.readouterr().out


def test_bad_target_observation_does_not_cancel_buy(tmp_path, monkeypatch):
    monkeypatch.setenv('TAKE_PROFIT_1_PCT','invalid')
    connect = observer.connect
    with patch.object(observer, 'connect', side_effect=lambda: connect(str(tmp_path/'a.db'))):
        expected={'executions':[]}
        assert observer.observe_auto_buy(lambda picks:expected,[]) is expected


def test_original_exception_is_not_swallowed(tmp_path):
    connect = observer.connect
    with patch.object(observer, 'connect', side_effect=lambda: connect(str(tmp_path/'a.db'))):
        with pytest.raises(RuntimeError, match='original failure'):
            observer.observe_auto_buy(lambda picks: (_ for _ in ()).throw(RuntimeError('original failure')), [])


def test_quote_audit_runs_after_buy_without_network_and_cannot_cancel_it(tmp_path):
    events=[];connect=observer.connect;path=str(tmp_path/'audit.db')
    expected={'executions':[]}
    def buy(*args,**kwargs):events.append('buy');return expected
    def saved(*args,**kwargs):events.append('saved_quotes');raise OSError('locked research database')
    with patch.object(data_store,'virtual_buy',side_effect=buy),patch.object(data_store,'query_rows',return_value=[]), \
         patch.object(observer,'connect',side_effect=lambda:connect(path)), \
         patch('stock_alarm.research_data_collect.collect_quotes',side_effect=AssertionError('network on buy path')), \
         patch('stock_alarm.research_data_collect.saved_quotes',side_effect=saved):
        assert observer.observe_auto_buy(lambda picks:data_store.virtual_buy([candidate()]),[]) is expected
    assert events==['buy','saved_quotes']
    with closing(connect(path)) as db:
        payload=json.loads(db.execute('SELECT payload FROM buy_attempts').fetchone()[0])
    assert payload['quote_observation']=={'state':'DATA_REQUIRED','error_type':'OSError'}


def test_no_data_is_not_mislabeled_as_budget_limit():
    p = {'picks': [{'ticker': 'X'}], 'auto_enabled': True, 'state': {'cash': 100}}
    assert research.decisions(p)[0]['reason'] == 'unconfirmed'
    p['risk_allowed'] = False
    assert research.decisions(p)[0]['reason'] == 'risk_halt'


def test_budget_downside_and_costs_are_measured():
    p = {'budgets': {'baseline': 0, 'higher_cap': 10000, 'relaxed_min_fill': 5000}, 'equity': 100000}
    result = research.budget_outcomes(p, rows([(100, 90)]))
    assert result['1']['difference_pct']['higher_cap'] < 0
    assert result['1']['quantities']['higher_cap'] == 99
    assert '10' not in result


def test_rotation_can_lose_to_retaining_old_winner():
    result = research.rotation_outcomes({'quantity': 100}, rows([(100, 100)]), rows([(100, 120)]))
    assert result['1']['difference_pct']['rotate'] < -20
    with pytest.raises(ValueError, match='align'):
        research.rotation_outcomes({'quantity': 100}, rows([(100, 100)]), rows([(100, 100)], date(2026, 10, 2)))


def test_reentry_waits_for_two_closes_then_uses_next_open():
    history = rows([(100, 100)] * 20 + [(100, 110), (110, 120), (150, 150), (160, 160)])
    signal = (date(2026, 10, 1) + timedelta(days=19)).isoformat()
    result = research.reentry_outcomes({'sale': {'proceeds': 10000}}, history, signal)
    assert result['1']['entry_days']['trend_reentry'] is None
    assert result['3']['entry_days']['trend_reentry'] == history[22][0]
    assert result['3']['entry_days']['wait_three'] is None
    assert result['3']['difference_pct']['trend_reentry'] < 0  # flat since fill, costs still paid


def test_case_collection_deduplicates_and_rejects_held_state_mismatch(tmp_path):
    path = str(tmp_path/'research.db')
    p = {'profile':'aggressive', 'source_db':'missing', 'observed_at':'2026-10-19T14:00:00', 'auto_enabled':True,
         'risk_allowed':True, 'candidates':[candidate()], 'picks':[candidate()], 'state':state(),
         'max_position':30, 'min_fill':.5, 'executions':[], 'evidence':{}, 'sectors':{},
         'budget_plan':observer.allocation_plan([candidate()], state(), 30, .5)}
    with closing(observer.connect(path)) as db:
        db.execute('INSERT INTO buy_attempts VALUES (?,?,?,?,?)', ('one',p['observed_at'],'aggressive',json.dumps(p),None))
        db.commit()
    assert research.harvest(path, {'aggressive':str(tmp_path/'missing.db')}) == 1
    assert research.harvest(path, {'aggressive':str(tmp_path/'missing.db')}) == 0
    with closing(observer.connect(path)) as db:
        assert db.execute('SELECT count(*) FROM cases').fetchone()[0] == 1


def test_grouping_uses_frozen_context_and_only_completed_cases(tmp_path):
    path = str(tmp_path/'research.db')
    with closing(observer.connect(path)) as db:
        for key, done in [('a', 1), ('b', 0)]:
            db.execute('INSERT INTO cases(case_id,kind,profile,ticker,signal_day,payload,outcomes,complete) VALUES (?,?,?,?,?,?,?,?)',
                       (key,'budget','aggressive','X','2026-10-19',json.dumps({'context':{'regime':'bear','sector':'반도체'}}),
                        json.dumps({'10':{'difference_pct':{'higher_cap':-1}}}),done))
        db.commit()
    groups = research.grouped_results(path, str(tmp_path/'missing.db'), str(tmp_path/'missing.db'))
    assert len(groups) == 1 and groups[0]['count'] == 1 and groups[0]['sector'] == '반도체'


def test_empty_dashboard_does_not_imply_success_or_profit(tmp_path):
    page = research.dashboard_section(str(tmp_path/'missing.db'))
    assert '조건에 해당하는 사례' in page and '+0.000' not in page


def test_no_fill_is_marked_excluded_instead_of_a_successful_zero_return():
    p = {'budgets': {'baseline': 0, 'higher_cap': 10, 'relaxed_min_fill': 5}, 'equity': 100000}
    result = research.budget_outcomes(p, rows([(100, 100)]))
    assert result['1']['skipped'] == 'all_variants_unfilled'
