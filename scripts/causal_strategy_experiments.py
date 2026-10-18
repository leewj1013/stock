"""Research-only signal policies. Never installed on a live account."""
import inspect
from statistics import mean, pstdev
import textwrap

from stock_alarm import benchmark_comparison as bc


def reentry_simulator():
    # Observe completed exits inside this run, before the existing signal selection.
    source = textwrap.dedent(inspect.getsource(bc.PortfolioSimulator.run))
    anchor = '        blocked = set(positions) | set(pending)'
    assert source.count(anchor) == 1, 'simulator changed: review the research hook'
    source = source.replace(anchor, '        candidates.observe(day, trades)\n' + anchor)
    namespace = dict(vars(bc))
    exec(compile(source, __file__, 'exec'), namespace)
    return type('ReentrySimulator', (bc.PortfolioSimulator,), {'run': namespace['run']})


class ReentryCandidates(dict):
    def __init__(self, cache, engine):
        super().__init__({d: list(rows) for d, rows in cache.items()})
        self.engine = engine
        sessions = sorted(engine.regimes)
        self.previous_regime = {sessions[i]: engine.regimes[sessions[i-1]] for i in range(1, len(sessions))}
        self.seen = 0
        self.pending = {}
        self.signals = []

    def observe(self, day, trades):
        for trade in trades[self.seen:]:
            assert trade.exit_date <= day
            if '20일선' in (trade.exit_reason or '') and self.previous_regime.get(trade.exit_date) == 'bear':
                self.pending[trade.ticker] = trade.exit_date
        self.seen = len(trades)
        for ticker, exit_day in list(self.pending.items()):
            item = self.engine.by_date[ticker].get(day)
            if item is None:
                continue
            index = item[0]
            age = index - self.engine.by_date[ticker][exit_day][0]
            if age > 10:
                del self.pending[ticker]
            elif 1 <= age <= 10:
                history = self.engine._history(ticker, day, 20)
                if len(history) == 20 and history[-1][4] > mean(r[4] for r in history):
                    self[day] = [r for r in self.get(day, []) if r['ticker'] != ticker] + [
                        dict(ticker=ticker, name=self.engine.names[ticker], score=999., volume_ratio=0., signal_date=day)]
                    self.signals.append(dict(ticker=ticker, exit_date=exit_day, signal_date=day))
                    del self.pending[ticker]


def volatility(history):
    if len(history) < 61 or any(r[4] <= 0 for r in history[-61:]):
        return None
    closes = [r[4] for r in history[-61:]]
    return pstdev(b/a-1 for a,b in zip(closes, closes[1:]))


def high_vol_candidates(cache, engine):
    """Update last month's threshold only after observing a new calendar month."""
    filtered, thresholds = {}, {}
    previous = None
    threshold = None
    for day in sorted(cache):
        if previous and previous[:7] != day[:7]:
            vols = []
            for ticker in engine.names:
                history = engine._history(ticker, previous, 62)
                if len(history) < 62 or mean(r[4]*r[5] for r in history[-20:]) < 1e9:
                    continue
                value = volatility(history)
                if value is not None:
                    vols.append(value)
            vols.sort()
            threshold = vols[int(len(vols)*.8)] if vols else None
            thresholds[previous] = dict(threshold=threshold, eligible=len(vols))
        kept = []
        for row in cache[day]:
            history = engine._history(row['ticker'], day, 62)
            value = volatility(history) if len(history) >= 62 else None
            if threshold is None or value is None or value <= threshold:
                kept.append(row)
        filtered[day] = kept
        previous = day
    return filtered, thresholds
