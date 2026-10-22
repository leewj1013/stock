"""Pre-registered 52-week-high proximity test (reports/research/2026-10-03-high52w.md). Read-only."""
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


ic, spread, ns, vol_ic, prox_vol, top_m, bot_m = [], [], [], [], [], [], []
pmin, pmax = 9.0, -9.0
for n, t in enumerate(ends[:-1]):
    nxt = ends[n + 1]
    entry = kd[kd.index(t) + 1]
    mret = k[nxt][4] / k[entry][1] - 1
    obs, vobs = [], []
    for tk, rows in px.items():
        ix = idx[tk]
        if t not in ix or entry not in ix or nxt not in ix:
            continue
        i = ix[t]
        if i < 61:
            continue
        w60 = rows[i - 60:i + 1]
        if mean(r[4] * r[5] for r in w60[-20:]) < 1e9 or any(r[4] <= 0 for r in w60):
            continue
        e = rows[ix[entry]][1]
        if e <= 0:
            continue
        ex = (rows[ix[nxt]][4] / e - 1 - mret) * 100
        vol = pstdev([w60[j][4] / w60[j - 1][4] - 1 for j in range(1, 61)])
        vobs.append((vol, ex))  # sanity (a): low_vol reproduction, same filter as low_vol.py
        if i < 249:
            continue
        w = rows[i - 249:i + 1]
        if any(r[4] <= 0 for r in w):
            continue
        hi = max(r[2] for r in w)
        if hi <= 0:
            continue
        prox = rows[i][4] / hi
        obs.append((prox, ex, vol))
    if len(vobs) >= 50:
        vol_ic.append((t, spearman([a for a, _ in vobs], [b for _, b in vobs])))
    if len(obs) < 50:
        continue
    pmin = min(pmin, min(o[0] for o in obs)); pmax = max(pmax, max(o[0] for o in obs))
    ic.append((t, spearman([o[0] for o in obs], [o[1] for o in obs])))
    prox_vol.append(spearman([o[0] for o in obs], [o[2] for o in obs]))
    obs.sort()
    q = len(obs) // 10
    bot, top = mean(o[1] for o in obs[:q]), mean(o[1] for o in obs[-q:])
    top_m.append(top); bot_m.append(bot)
    spread.append((t, top - bot - 0.6))  # 0.3% round trip on each of the two legs
    ns.append(len(obs))

tests = []
for name, series in (("IC(prox52w, next-month excess)", ic), ("top10 - bottom10 spread %p/month (net 0.6)", spread)):
    vals = [v for _, v in series]
    p = float(newey_west_mean_test(vals, 1, "two-sided")["p_value"])
    h1 = [v for t, v in series if t < "20220601"]; h2 = [v for t, v in series if t >= "20220601"]
    tests.append((name, len(vals), mean(vals), p, mean(h1), mean(h2), len(h1), len(h2)))
for (name, n, m, p, a, b, n1, n2), q in zip(tests, benjamini_hochberg([t[3] for t in tests])):
    print(f"{name}: months={n} mean={m:+.4f} p={p:.4g} q={float(q):.4g} | 2018-22 ({n1}) {a:+.4f} | 2022-26 ({n2}) {b:+.4f}")
print(f"first signal {ic[0][0]} last signal {ic[-1][0]}; stocks/month min {min(ns)} median {median(ns)} max {max(ns)}")
print(f"top10 mean excess {mean(top_m):+.3f}%  bottom10 mean excess {mean(bot_m):+.3f}% (gross, per month)")
print(f"sanity (a) vol IC 2018-06..2026-07: months={len(vol_ic)} mean={mean(v for _, v in vol_ic):+.4f} (expected -0.146)")
print(f"sanity (b) proximity range [{pmin:.4f}, {pmax:.4f}]")
print(f"sanity (c) mean monthly spearman(prox, vol60) {mean(prox_vol):+.3f}")
