"""Offline causal-signal correction audit; outputs only to reports/revalidation."""
import json
import os
from pathlib import Path
import socket
from functools import lru_cache
from dataclasses import asdict

from revalidate_execution_timing import ROOT, fixed as bc, BacktestEngine, dump, compare, hashlib, benjamini_hochberg
from causal_strategy_experiments import ReentryCandidates, high_vol_candidates, reentry_simulator

SOURCE = ROOT / 'reports/revalidation/2026-10-17-execution-timing'
PREVIOUS = ROOT / 'reports/revalidation/2026-10-17-strategy-variants'
OUT = ROOT / 'reports/revalidation/2026-10-18-causal-signals'


def main():
    os.chdir(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = json.loads((SOURCE/'kr_all/inputs.json').read_text('utf-8'))
    for key in list(os.environ):
        if key.startswith(('SELL_', 'TAKE_PROFIT_', 'RISK_', 'CORRELATION', 'CORRELATED_', 'MAX_', 'MIN_', 'BACKTEST_', 'VOLUME_')) or key in ('TOP_N','EXECUTION_COST_BPS'):
            del os.environ[key]
    os.environ.update(inputs['env'])
    os.environ['KOREAN_STOCK_NAMES'] = '0'
    def offline(*args, **kwargs): raise RuntimeError('offline research: no network')
    socket.create_connection = offline
    data = Path(inputs['data'])
    def verify_prices():
        assert all(hashlib.sha256((data/n).read_bytes()).hexdigest()==h for n,h in inputs['sha256'].items())
    verify_prices()
    manifest = json.loads((SOURCE/'manifest.json').read_text('utf-8'))
    assert all(hashlib.sha256((ROOT/'stock_alarm'/n).read_bytes()).hexdigest()==h for n,h in manifest['shared_source_sha256'].items())
    cache = json.loads((SOURCE/'kr_all/candidates.json').read_text('utf-8'))
    assert hashlib.sha256((SOURCE/'kr_all/candidates.json').read_bytes()).hexdigest() == json.loads((PREVIOUS/'kr_all/inputs.json').read_text('utf-8'))['candidate_sha256']
    engine = BacktestEngine(data_dir=data,report_dir=ROOT/'reports/research/scripts/regime_2018',names=inputs['universe'],score_weights=inputs['weights'],pit_store_path=Path(inputs['pit']))
    engine._market_up_ratio = lru_cache(maxsize=None)(engine._market_up_ratio)
    print('Inputs verified; engine loaded',flush=True)
    filtered, thresholds = high_vol_candidates(cache,engine)
    dump(OUT/'thresholds.json', thresholds)
    provider = ReentryCandidates(cache,engine)
    results, runs = {}, {}
    for name, cls, candidates in [('baseline',bc.PortfolioSimulator,cache),('reentry',reentry_simulator(),provider),('high_vol_exclusion',bc.PortfolioSimulator,filtered)]:
        sim=cls(engine,100_000_000,10,apply_live_constraints=False)
        sim.days=sorted(cache)
        result=sim.run('stock_alarm',candidates,20260828)
        runs[name]=result
        results[name]={'metrics':bc.equity_metrics(result.daily_equity,result.trades,engine.regimes,'all')}
        dump(OUT/f'{name}_equity.json',result.daily_equity)
        dump(OUT/f'{name}_events.json',result.events)
        dump(OUT/f'{name}_trades.json',[asdict(t) for t in result.trades])
        print(name,results[name],flush=True)
    previous=json.loads((PREVIOUS/'kr_all/results.json').read_text('utf-8'))['fixed']
    assert results['baseline']['metrics']=={k:v for k,v in previous['no_gate']['metrics'].items() if k in results['baseline']['metrics']}
    for signal in provider.signals:
        assert signal['exit_date'] < signal['signal_date']
        assert any(t.ticker==signal['ticker'] and t.exit_date==signal['exit_date'] and '20일선' in t.exit_reason for t in runs['reentry'].trades)
    dump(OUT/'reentry_signals.json',provider.signals)
    baseline=bc.daily_return_map(runs['baseline'])
    for name in ('reentry','high_vol_exclusion'):
        results[name]['vs_baseline']=compare(bc.daily_return_map(runs[name]),baseline)
        old=json.loads((PREVIOUS/'kr_all'/f'fixed_{name}_equity.json').read_text('utf-8'))
        results[name]['vs_previous_method']=compare(bc.daily_return_map(runs[name]),bc.daily_return_map(bc.PortfolioResult(name,old,[])))
    for name,q in zip(('reentry','high_vol_exclusion'),benjamini_hochberg([results[n]['vs_baseline']['p_two_sided'] for n in ('reentry','high_vol_exclusion')])):
        results[name]['q_two_corrected_tests']=q
    verify_prices()
    results['checks']={'baseline_reproduced':True,'all_reentries_have_actual_exit':True,'reentry_signals':len(provider.signals),
        'excluded_candidates':sum(len(cache[d])-len(filtered[d]) for d in cache),'total_candidates':sum(map(len,cache.values()))}
    dump(OUT/'results.json',results)
    files=[Path(__file__),ROOT/'scripts/causal_strategy_experiments.py',SOURCE/'kr_all/inputs.json',SOURCE/'kr_all/candidates.json',OUT/'results.json',OUT/'PROTOCOL.md']
    dump(OUT/'manifest.json',{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    for p in files[:2]: (OUT/p.name).write_bytes(p.read_bytes())
    lines=['# 재진입·고변동성 제외의 시간 순서 수정 — 2026-10-18','',
        '국내 전체 2,941종목, 2018-06-29~2026-08-28, 초기 1억원·슬롯 10%·위험중단 끔. 가격·후보·설정·공통 엔진은 직전 재검증과 동일하며 해시와 기본 전략 재현을 확인했습니다.','',
        '## 변경 내용','',
        '- 재진입: 같은 실행에서 실제 완료된 하락장 20일선 매도만 관찰합니다. 이후 10개 종목 거래일 안의 첫 종가 재돌파에 신호를 내고, 기존 휴지기·한도와 다음 거래일 시가 체결을 그대로 적용합니다. 과거 실행의 신호 합집합이나 반복 수렴을 사용하지 않습니다.',
        '- 고변동성 제외: 새 달의 첫 관측 거래일에 직전 거래일 자료만으로 유동성 집단과 60일 변동성 80백분위를 확정합니다. 다음 날 시가와 이후 청산 가격 존재 여부는 검사하지 않습니다. 종목 자체의 변동성은 신호일 종가까지로 계산합니다.',
        '- 운영 엔진은 수정하지 않았습니다. 연구용 실행 복사본에 매도 관찰 지점 하나만 삽입하며, 삽입 위치가 달라지면 검사를 실패시킵니다.','',
        '## 결과','', '| 구성 | 직전 방식 수익% | 수정 수익% | 수정 MDD% | 기준 대비 p | BH q |','|---|---:|---:|---:|---:|---:|']
    for name in ('baseline','reentry','high_vol_exclusion'):
        m=results[name]['metrics']; old=previous['no_gate' if name=='baseline' else name]['metrics']
        p=f"{results[name]['vs_baseline']['p_two_sided']:.5f}" if name!='baseline' else '—'
        q=f"{results[name]['q_two_corrected_tests']:.5f}" if name!='baseline' else '—'
        lines.append(f"| {name} | {old['total_return_pct']:.4f} | {m['total_return_pct']:.4f} | {m['mdd_pct']:.4f} | {p} | {q} |")
    lines += ['', 'HAC lag5 양측 검정, 이번 두 수정안에 대한 BH 보정입니다. 이전 저변동 매수 실험과 묶었던 q와 검정 집단이 다릅니다. 채택 기준은 q<0.05, 평균 개선 양수, 전·후반 개선 모두 양수로 사전에 고정했습니다.']
    for name in ('reentry','high_vol_exclusion'):
        v=results[name]; c=v['vs_baseline']; passed=v['q_two_corrected_tests']<.05 and min(c['mean_pp'],c['first_half_pp'],c['second_half_pp'])>0
        lines += ['', f"- {name}: 기준 통과={passed}, 전반/후반 일평균 차이 {c['first_half_pp']:.5f}/{c['second_half_pp']:.5f}%p."]
    lines += ['',f"재진입 신호 {len(provider.signals)}건 모두 실제 발생한 이전 매도와 연결됩니다. 후보 {results['checks']['total_candidates']}건 중 고변동성 {results['checks']['excluded_candidates']}건을 제외했습니다.",'',
        '## 검증 및 한계','',
        '미래 가격 삭제·변경에 대한 신호 불변성, 매도 없는 재진입 금지, 10일 만료, 실제 시뮬레이터의 기간 절단 후 거래·자산 일치 검사를 tests/test_causal_strategy_experiments.py에 추가했습니다. 전체 검사 결과는 tests.log를 참조하세요.',
        '기존 종가 매도 체결 가정과 종목군·가격 원자료의 한계까지 해결한 것은 아닙니다. 실제 주문·운영 계좌 변경·배포는 하지 않았습니다. 원본 실험은 역사적 재현용으로 보존하며 앞으로 두 실험은 이 수정 스크립트를 사용합니다.','',
        '재실행: 저장소 루트에서 `.venv/Scripts/python.exe scripts/revalidate_causal_signals.py`. 입력 검증 실패 시 과거 파일을 덮어 맞추지 말고 새 입력에 대한 별도 연구로 분리하세요.','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('DONE',flush=True)


if __name__=='__main__': main()
