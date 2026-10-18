"""Offline paired rerun of existing experiments using the execution-timing snapshot.

Run from the repository root: python scripts/revalidate_strategy_variants.py kr_all|kr_watch
Research outputs only; no live account/config writes.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time
from functools import lru_cache
from statistics import mean, pstdev, stdev
from bisect import bisect_left
import math

from revalidate_execution_timing import ROOT, fixed as bc, dump, compare, BacktestEngine
from revalidate_execution_timing import hashlib, benjamini_hochberg, newey_west_mean_test
from stock_alarm.risk_release_policy import load_risk_release_variants
from stock_alarm.sell_check import SellAlert, _evaluate_position, check_position

SOURCE = ROOT / 'reports/revalidation/2026-10-17-execution-timing'
OUTPUT = ROOT / 'reports/revalidation/2026-10-17-strategy-variants'


def functions(path, names, namespace):
    # Reuse research functions without their top-level network calls or file writes.
    tree = ast.parse(path.read_text('utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


def main(dataset):
    os.chdir(ROOT)
    out = OUTPUT / dataset
    out.mkdir(parents=True, exist_ok=True)
    inputs = json.loads((SOURCE / dataset / 'inputs.json').read_text('utf-8'))
    for key in list(os.environ):
        if key.startswith(('SELL_', 'TAKE_PROFIT_', 'RISK_', 'CORRELATION', 'CORRELATED_', 'MAX_', 'MIN_', 'BACKTEST_', 'VOLUME_')) or key in ('TOP_N', 'EXECUTION_COST_BPS'):
            del os.environ[key]
    os.environ.update(inputs['env'])
    os.environ['KOREAN_STOCK_NAMES'] = '0'
    def no_network(*args, **kwargs):
        raise RuntimeError('offline audit: network disabled')
    socket.create_connection = no_network
    data = Path(inputs['data'])
    def check_prices():
        assert all(hashlib.sha256((data / n).read_bytes()).hexdigest() == h for n, h in inputs['sha256'].items()), 'price snapshot changed'
    check_prices()
    manifest = json.loads((SOURCE / 'manifest.json').read_text('utf-8'))
    for name, digest in manifest['shared_source_sha256'].items():
        assert hashlib.sha256((ROOT / 'stock_alarm' / name).read_bytes()).hexdigest() == digest, name
    spec = importlib.util.spec_from_file_location('stock_alarm._variants_legacy', SOURCE / 'legacy_benchmark_comparison.py')
    legacy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = legacy
    spec.loader.exec_module(legacy)
    regimes = ROOT / ('reports/research/scripts/regime_2018' if dataset == 'kr_all' else 'reports/backtest')
    engine = BacktestEngine(data_dir=data, report_dir=regimes, names=inputs['universe'], score_weights=inputs['weights'], pit_store_path=Path(inputs['pit']))
    engine._market_up_ratio = lru_cache(maxsize=None)(engine._market_up_ratio)
    cache = json.loads((SOURCE / dataset / 'candidates.json').read_text('utf-8'))
    days = sorted(cache)
    policies = load_risk_release_variants() | load_risk_release_variants(Path('config/peak_decay_variants.json'))
    sector_path = ROOT / '.cache/sector_mapping.json'
    sectors = json.loads(sector_path.read_text('utf-8'))
    dump(out / 'sector_snapshot.json', sectors)
    dump(out / 'inputs.json', {'baseline_inputs': str(SOURCE / dataset / 'inputs.json'), 'policies': policies,
        'candidate_sha256': hashlib.sha256((SOURCE / dataset / 'candidates.json').read_bytes()).hexdigest(),
        'mapped': len(set(inputs['universe']) & set(sectors['mapping']))})
    core_ns = {'mean': mean, 'pstdev': pstdev, 'math': math, 'DAILY_FEE': .0015 / 252, 'MOVE_COST': .0005}
    functions(ROOT / 'reports/backtest/exit_policy_study/scripts/core_satellite.py', ['split_hold', 'split_quarterly', 'cash_sweep'], core_ns)
    exit_ns = {'SellAlert': SellAlert, '_evaluate_position': _evaluate_position, 'PRODUCTION_CHECK': check_position}
    functions(ROOT / 'reports/backtest/exit_policy_study/scripts/exit_study.py', ['make_alert', 'base_eval', 'v_no_take_profit', 'v_current'], exit_ns)
    previous_regime = {days[i]: engine.regimes.get(days[i-1]) for i in range(1, len(days))}
    def candidate_exit(position, end_day, *args, **kwargs):
        fn = exit_ns['v_no_take_profit'] if previous_regime.get(end_day.isoformat()) == 'bull' else exit_ns['v_current']
        return fn(position, end_day, *args, **kwargs)
    assert all(abs(x) < 1e-12 for x in core_ns['split_quarterly'](['2020-01-01', '2020-04-01'], dict.fromkeys(['2020-01-01', '2020-04-01'], 0), dict.fromkeys(['2020-01-01', '2020-04-01'], 0), 0))
    results = {}
    returns = {}
    def record(label, name, result):
        metrics = bc.equity_metrics(result.daily_equity, result.trades, engine.regimes, 'all')
        flags = [s['defensive_mode'] for s in result.daily_state]
        longest = current = 0
        for flag in flags:
            current = current + 1 if flag else 0
            longest = max(longest, current)
        metrics.update(bear_mdd_pct=bc.equity_metrics(result.daily_equity, result.trades, engine.regimes, 'bear')['mdd_pct'],
                       halt_days=sum(flags), longest_halt_days=longest, halted_at_end=bool(flags and flags[-1]),
                       sector_limited=sum(s.get('sector_limited_count', 0) for s in result.daily_state))
        results[label][name] = {'metrics': metrics}
        returns[label][name] = bc.daily_return_map(result)
        dump(out / f'{label}_{name}_equity.json', result.daily_equity)
        print(dataset, label, name, metrics, flush=True)
    def run(module, **kwargs):
        sim = module.PortfolioSimulator(engine, 100_000_000, 10, **kwargs)
        sim.days = days
        return sim.run('stock_alarm', cache, 20260828)
    filtered = None
    if dataset == 'kr_all':
        # Preserve the original threshold/long-only construction for comparability.
        # shortcut: next-open eligibility survives here; do not promote this to a live rule.
        px = {t: engine.rows[t] for t in inputs['universe']}
        k = {r[0]: r for r in engine.rows['KOSPI']}
        month_end = {}
        for d in sorted(k): month_end[d[:6]] = d
        ns = dict(px=px, k=k, kd=sorted(k), ends=[v for m,v in sorted(month_end.items()) if '201806' <= m <= '202608'],
                  idx={t: {r[0]: i for i,r in enumerate(rows)} for t,rows in px.items()},
                  mean=mean, pstdev=pstdev, stdev=stdev, math=math, time=time, t0=time.time())
        low_path = ROOT / 'reports/research/scripts/low_vol_followups.py'
        functions(low_path, ['vol_at', 'summary'], ns)
        source = low_path.read_text('utf-8')
        exec(source[source.index('# ---- test 1'):source.index('# ---- test 2')], ns)
        thresholds = ns['threshold']; keys = sorted(thresholds)
        filtered = {}
        for day, candidates in cache.items():
            ymd = day.replace('-', ''); m = bisect_left(keys, ymd) - 1
            kept = []
            for row in candidates:
                i = ns['idx'][row['ticker']].get(ymd)
                vol = ns['vol_at'](row['ticker'], i) if m >= 0 and i is not None and i >= 61 else None
                if vol is None or vol <= thresholds[keys[m]]: kept.append(row)
            filtered[day] = kept
        dump(out / 'low_vol_monthly.json', {key: ns[key] for key in ('port', 'kos', 'ew', 'threshold')})
        t1p = newey_west_mean_test([v for _,v in ns['excess1']], 1, 'two-sided')['p_value']
        prev = {days[i]: engine.regimes.get(days[i-1]) for i in range(1, len(days))}
        re_ns = {'px': px, 'cache': cache, 'meta': inputs['universe'], 'mean': mean,
                 'prev_regime': prev.get, 'iso': lambda d: f'{d[:4]}-{d[4:6]}-{d[6:]}'}
        functions(ROOT / 'reports/research/scripts/bear_ma20_reentry.py', ['reentry_signals', 'with_reentries'], re_ns)
    for label, module in [('legacy', legacy), ('fixed', bc)]:
        results[label], returns[label] = {}, {}
        satellites = {}
        for name, kwargs in [('no_gate', {'apply_live_constraints': False}), *[(name, {'risk_release_policy': policy}) for name,policy in policies.items()]]:
            result = run(module, **kwargs)
            record(label, name, result)
            if name in ('no_gate', 'variant_A', 'variant_C_20_30'): satellites[name] = result
        if dataset == 'kr_watch':
            for cap in (40, 30, 20):
                record(label, f'sector_{cap}', run(module, apply_sector_limit=True, sector_group_cap_pct=cap, sector_by_ticker=sectors['mapping']))
        else:
            sim = module.PortfolioSimulator(engine, 100_000_000, 10, apply_live_constraints=False); sim.days = days
            record(label, 'high_vol_exclusion', sim.run('stock_alarm', filtered, 20260828))
            signals = re_ns['reentry_signals'](satellites['no_gate'].trades)
            converged = False
            for iteration in range(1, 6):
                result = sim.run('stock_alarm', re_ns['with_reentries'](signals), 20260828)
                new = signals | re_ns['reentry_signals'](result.trades)
                if new == signals:
                    converged = True; break
                if iteration < 5: signals = new
            record(label, 'reentry', result)
            actual = re_ns['reentry_signals'](result.trades)
            results[label]['reentry'].update(converged=converged, iterations=iteration, signals=len(signals),
                stale_signals=len(signals-actual), executed=sum((t.signal_date,t.ticker) in signals for t in result.trades))
        production_check = module.check_position
        module.check_position = candidate_exit
        try:
            candidate = run(module, risk_release_policy=policies['variant_C_20_30'])
        finally:
            module.check_position = production_check
        record(label, 'bull_no_tp_cooldown', candidate)
        satellites['bull_no_tp_cooldown'] = candidate
        sim = module.PortfolioSimulator(engine, 100_000_000, 10); sim.days = days
        index = sim.buy_and_hold('index', ['KOSPI'])
        rk = bc.daily_return_map(index)
        # Same index fees and daily-path mixing assumptions as the saved experiment.
        for name, sat in satellites.items():
            rs = bc.daily_return_map(sat)
            mixes = {f'core_{int(c*100)}_{layout}': core_ns[fn](days, rk, rs, c)
                     for c in (.3,.5,.7) for layout,fn in [('hold','split_hold'),('qtr','split_quarterly')]}
            mixes.update({f'sweep_{int(s*100)}': core_ns['cash_sweep'](days,rk,rs,{r['date']:r['cash_weight'] for r in sat.daily_state},s) for s in (.5,1)})
            for mix, daily in mixes.items():
                eq = 100_000_000; rows = []
                for day, ret in zip(days, daily):
                    eq *= 1 + ret; rows.append({'date': day, 'equity': eq, 'base_equity': 100_000_000})
                record(label, f'{name}_{mix}', bc.PortfolioResult(mix, rows, []))
        record(label, 'index', index)
        for name in results[label]:
            baseline = 'no_gate' if name in ('reentry','high_vol_exclusion') or name.startswith('no_gate_') else 'variant_A'
            results[label][name]['vs_baseline'] = compare(returns[label][name], returns[label][baseline])
            results[label][name]['vs_index'] = compare(returns[label][name], returns[label]['index'])
        risk_names = [name for name in policies if name != 'variant_A']
        for name,q in zip(risk_names, benjamini_hochberg([results[label][n]['vs_baseline']['p_two_sided'] for n in risk_names])):
            results[label][name]['risk_family_q'] = q
        if dataset == 'kr_all':
            results[label]['high_vol_exclusion']['original_two_test_family_q'] = benjamini_hochberg([t1p,results[label]['high_vol_exclusion']['vs_baseline']['p_two_sided']])[1]
        else:
            tests = []
            for cap in (40,30,20):
                for scope in ('all','bull','bear','sideways'):
                    differences = [(returns[label][f'sector_{cap}'][d]-returns[label]['variant_A'][d])*100 for d in days if scope == 'all' or engine.regimes.get(d) == scope]
                    tests.append({'cap': cap, 'scope': scope, 'mean_pp': mean(differences), 'p': newey_west_mean_test(differences, 5, 'greater')['p_value']})
            for test,q in zip(tests,benjamini_hochberg([t['p'] for t in tests])): test['q'] = q
            dump(out / f'{label}_sector_tests.json', tests)
    check_prices()
    previous = json.loads((SOURCE / dataset / 'results.json').read_text('utf-8'))
    for label in results:
        name = 'no_gate' if dataset == 'kr_all' else 'variant_A'
        assert results[label][name]['metrics']['total_return_pct'] == previous['results'][label]['baseline']['metrics']['total_return_pct'], 'baseline mismatch'
    dump(out / 'results.json', results)
    print('DONE', dataset, flush=True)


if __name__ == '__main__':
    assert sys.argv[1] in ('kr_all', 'kr_watch')
    main(sys.argv[1])
