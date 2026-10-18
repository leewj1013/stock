from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from scripts.causal_strategy_experiments import ReentryCandidates, high_vol_candidates, reentry_simulator


class Engine:
    def __init__(self, count=100):
        self.days = [(date(2020, 1, 1)+timedelta(days=i)).isoformat() for i in range(count)]
        self.names = {'A': 'A', 'B': 'B'}
        self.rows = {t: [[d.replace('-', ''), 100, 110, 90, 100+(i % 3 if t == 'B' else 0), 20_000_000]
                         for i,d in enumerate(self.days)] for t in ('A', 'B', 'KOSPI')}
        if count > 75:
            self.rows['A'][75][4] = 110
        self.by_date = {t: {d: (i,r) for i,(d,r) in enumerate(zip(self.days,rows))} for t,rows in self.rows.items()}
        self.regimes = dict.fromkeys(self.days, 'bear')
        self.top_n = 1
        self.minimum_score = self.cost_pct = 0

    def _history(self, ticker, day, length=91):
        item = self.by_date[ticker].get(day)
        return [] if item is None else self.rows[ticker][max(0,item[0]-length+1):item[0]+1]

    def _market_up_ratio(self, day): return 1.
    def _benchmark_trade_return(self, entry, exit): return 0.


def cache(engine):
    return {d: [dict(ticker='A',name='A',score=99.,volume_ratio=2.,signal_date=d)] for d in engine.days[65:]}


def test_threshold_ignores_missing_or_changed_future_prices():
    full, short = Engine(), Engine(93)
    full.rows['A'][91][1] = 0
    full.rows['A'][94][4] = 1_000_000
    a, thresholds = high_vol_candidates(cache(full), full)
    b, truncated_thresholds = high_vol_candidates(cache(short), short)
    assert {d:a[d] for d in b} == b
    assert thresholds == truncated_thresholds
    assert thresholds['2020-03-31']['eligible'] == 2


def test_reentry_requires_actual_exit_and_expires():
    engine = Engine()
    empty = dict.fromkeys(engine.days, [])
    candidates = ReentryCandidates(empty, engine)
    for day in engine.days: candidates.observe(day, [])
    assert candidates.signals == []
    trade = SimpleNamespace(ticker='A',exit_date=engine.days[70],exit_reason='20일선 이탈')
    candidates = ReentryCandidates(empty, engine)
    for day in engine.days[70:]: candidates.observe(day, [trade])
    assert candidates.signals == [dict(ticker='A',exit_date=engine.days[70],signal_date=engine.days[75])]
    assert not candidates.pending
    trade.exit_date = engine.days[60]
    candidates = ReentryCandidates(empty, engine)
    for day in engine.days[60:]: candidates.observe(day,[trade])
    assert not candidates.signals and not candidates.pending
    trade.exit_date = engine.days[65]
    candidates = ReentryCandidates(empty, engine)
    for day in engine.days[65:]: candidates.observe(day,[trade])
    assert candidates.signals[0]['signal_date'] == engine.days[75]


def test_simulator_uses_its_own_exits_and_prefix_is_stable(monkeypatch):
    monkeypatch.setenv('SELL_RECOMMEND_COOLDOWN_DAYS','3')
    engine = Engine()
    original = dict.fromkeys(engine.days, [])
    original[engine.days[65]] = cache(engine)[engine.days[65]]
    def sell(position, day, *args, **kwargs):
        if day.isoformat() == engine.days[70]:
            return SimpleNamespace(sale_type='full',reason='20일선 이탈',stage='test')
    outputs = []
    with patch('stock_alarm.benchmark_comparison.check_position', side_effect=sell):
        for days in (engine.days, engine.days[:78]):
            truncated_engine = Engine(len(days))
            provider = ReentryCandidates(original,truncated_engine)
            sim = reentry_simulator()(truncated_engine,100_000,10,apply_live_constraints=False)
            sim.days = days
            result = sim.run('stock_alarm',provider,liquidate_at_end=False)
            outputs.append((result,provider))
    full, prefix = outputs
    assert full[0].daily_equity[:78] == prefix[0].daily_equity
    assert [e for e in full[0].events if e['date'] <= engine.days[77]] == prefix[0].events
    assert full[1].signals == prefix[1].signals
    assert [e['date'] for e in full[0].events if e['event']=='buy'] == [engine.days[66],engine.days[76]]
    assert original[engine.days[75]] == []
