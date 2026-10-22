"""Pre-registered low-volatility test (see reports/research/2026-09-30-low_vol.md). Read-only."""
import csv, math, os, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, pstdev
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\cyawj\Documents\stockAlarm"
os.chdir(ROOT); sys.path.insert(0, ROOT)
from stock_alarm.app import naver_rows
from stock_alarm.backtest_data import read_rows
from stock_alarm.statistical_validation import newey_west_mean_test, benjamini_hochberg

PIT = Path("data/backtest_expanded/pit_universe")
k = {r[0]: r for r in naver_rows("KOSPI", date(2017, 12, 1), date(2026, 9, 29), max_cache_age_seconds=3600)}
kd = sorted(k)
month_end = {}
for d in kd:
    month_end[d[:6]] = d
ends = [v for m, v in sorted(month_end.items()) if "201806" <= m <= "202608"]

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


ic, spread, ns = [], [], []
for n, t in enumerate(ends[:-1]):
    nxt = ends[n + 1]
    ki = kd.index(t)
    entry = kd[ki + 1]
    mret = k[nxt][4] / k[entry][1] - 1
    obs = []
    for tk, rows in px.items():
        ix = idx[tk]
        if t not in ix or entry not in ix or nxt not in ix:
            continue
        i = ix[t]
        if i < 61:
            continue
        window = rows[i - 60:i + 1]
        if mean(r[4] * r[5] for r in window[-20:]) < 1e9 or any(r[4] <= 0 for r in window):
            continue
        rets = [window[j][4] / window[j - 1][4] - 1 for j in range(1, len(window))]
        vol = pstdev(rets)
        e = rows[ix[entry]][1]
        if e <= 0:
            continue
        obs.append((vol, (rows[ix[nxt]][4] / e - 1 - mret) * 100))
    if len(obs) < 50:
        continue
    ic.append((t, spearman([a for a, _ in obs], [b for _, b in obs])))
    obs.sort()
    q = len(obs) // 10
    low, high = mean(b for _, b in obs[:q]), mean(b for _, b in obs[-q:])
    spread.append((t, low - high - 0.6))  # 0.3% round trip on each of the two legs
    ns.append(len(obs))

tests = []
for name, series in (("IC(vol, next-month excess)", ic), ("low10 - high10 spread %p/month", spread)):
    vals = [v for _, v in series]
    p = float(newey_west_mean_test(vals, 1, "two-sided")["p_value"])
    h1 = [v for t, v in series if t < "20220601"]; h2 = [v for t, v in series if t >= "20220601"]
    tests.append((name, len(vals), mean(vals), p, mean(h1), mean(h2)))
for (name, n, m, p, a, b), q in zip(tests, benjamini_hochberg([t[3] for t in tests])):
    print(f"{name}: months={n} mean={m:+.4f} p={p:.4f} q={float(q):.4f} | 2018-22 {a:+.4f} | 2022-26 {b:+.4f}")
print(f"stocks per month: min {min(ns)} median {sorted(ns)[len(ns)//2]} max {max(ns)}")
# sanity: low-vol decile should indeed have lower realized next-month dispersion
print("sanity: first month", ends[0], "last month", ends[-2])
