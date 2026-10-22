"""Pre-registered 1-week reversal test (reports/research/2026-10-13-reversal_1w.md). Read-only."""
import csv, math, os, sys
from datetime import date
from pathlib import Path
from statistics import mean, pstdev, median
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\cyawj\Documents\stockAlarm"
os.chdir(ROOT); sys.path.insert(0, ROOT)
from stock_alarm.app import naver_rows
from stock_alarm.backtest_data import read_rows
from stock_alarm.statistical_validation import newey_west_mean_test, benjamini_hochberg

PIT = Path("data/backtest_expanded/pit_universe")
k = {r[0]: r for r in naver_rows("KOSPI", date(2017, 12, 1), date(2026, 9, 29), max_cache_age_seconds=3600)}
kd = sorted(k)
first = next(i for i, d in enumerate(kd) if d >= "20180601")
sig = []
j = first
while j + 5 < len(kd) and kd[j + 5] <= "20260831":
    sig.append(j); j += 5

px = {}
for r in csv.DictReader(open(PIT / "tickers.csv", encoding="utf-8-sig")):
    rows = read_rows(r["ticker"], PIT / "ohlcv")
    if len(rows) > 80:
        px[r["ticker"]] = rows
idx = {t: {row[0]: i for i, row in enumerate(rows)} for t, rows in px.items()}


def ranks(v):
    o = sorted(range(len(v)), key=v.__getitem__); out = [0.0] * len(v); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
        for q in range(i, j + 1): out[o[q]] = (i + j) / 2
        i = j + 1
    return out


def spearman(x, y):
    rx, ry = ranks(x), ranks(y); mx, my = mean(rx), mean(ry)
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / den if den else None


ic, vol_ic, spread, ns, bot_g, top_g, allm = [], [], [], [], [], [], []
for j in sig:
    t, past, entry, exit_ = kd[j], kd[j - 5], kd[j + 1], kd[j + 5]
    mret = k[exit_][4] / k[entry][1] - 1
    obs = []
    for tk, rows in px.items():
        ix = idx[tk]
        if t not in ix or past not in ix or entry not in ix or exit_ not in ix:
            continue
        i = ix[t]
        if i < 60:
            continue
        w = rows[i - 19:i + 1]
        if mean(r[4] * r[5] for r in w) < 1e9:
            continue
        c0, c1, e = rows[ix[past]][4], rows[i][4], rows[ix[entry]][1]
        if c0 <= 0 or c1 <= 0 or e <= 0:
            continue
        ex = (rows[ix[exit_]][4] / e - 1 - mret) * 100
        w60 = rows[i - 60:i + 1]
        vol = pstdev([w60[m][4] / w60[m - 1][4] - 1 for m in range(1, 61)]) if all(r[4] > 0 for r in w60) else None
        obs.append((c1 / c0 - 1, ex, vol))
    if len(obs) < 50:
        continue
    ic.append((t, spearman([o[0] for o in obs], [o[1] for o in obs])))
    vv = [o for o in obs if o[2] is not None]
    vol_ic.append(spearman([o[2] for o in vv], [o[1] for o in vv]))
    allm.append(mean(o[1] for o in obs))
    o2 = sorted(obs, key=lambda o: o[0]); q = len(o2) // 10
    b, tp = mean(o[1] for o in o2[:q]), mean(o[1] for o in o2[-q:])
    bot_g.append(b); top_g.append(tp)
    spread.append((t, b - tp - 0.6)); ns.append(len(obs))

tests = []
for name, series in (("IC(ret5, next-5d excess)", ic), ("bottom10 - top10 spread %p/week (net 0.6)", spread)):
    vals = [v for _, v in series]
    p = float(newey_west_mean_test(vals, 4, "two-sided")["p_value"])
    h1 = [v for t, v in series if t < "20220601"]; h2 = [v for t, v in series if t >= "20220601"]
    tests.append((name, len(vals), mean(vals), p, mean(h1), mean(h2), len(h1), len(h2)))
for (name, n, m, p, a, b, n1, n2), q in zip(tests, benjamini_hochberg([t[3] for t in tests])):
    print(f"{name}: weeks={n} mean={m:+.4f} p={p:.4g} q={float(q):.4g} | 2018-22 ({n1}) {a:+.4f} | 2022-26 ({n2}) {b:+.4f}")
print(f"first signal {ic[0][0]} last signal {ic[-1][0]}; stocks/week min {min(ns)} median {median(ns)} max {max(ns)}")
print(f"gross weekly excess: bottom10 {mean(bot_g):+.3f}%  top10 {mean(top_g):+.3f}%  all {mean(allm):+.3f}%")
j0 = sig[0]; print(f"sanity (a) first: signal {kd[j0]} past {kd[j0-5]} entry {kd[j0+1]} exit {kd[j0+5]}")
print(f"sanity (b) vol60 IC weekly mean {mean(vol_ic):+.4f} (expect negative)")
