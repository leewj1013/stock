"""US dashboard page (served at /us). Read-only view of the US control account, market and research.

Kept apart from dashboard.py so a failure here cannot blank the domestic page.
"""
import csv
import os
from datetime import date, datetime

from .dashboard import e, read_text
from .market_summary import _read_us_cache, us_row_text
from .us_spy_hold import CAPITAL_USD, LEDGER_PATH

RESEARCH_DIR = "reports/research"
UNIVERSE_PATH = "data/backtest_us/universe.csv"
STALE_DAYS = 5  # weekend + a US holiday still leaves a fresh row within 5 days

CSS = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--alt:#f8fafc;--text:#111;--sub:#64748b;--line:#e2e8f0;--accent:#2563eb;--accent-bg:#eef6ff;--pos:#047857;--neg:#dc2626;--warn-bg:#fef3c7;--warn:#92400e}
@media(prefers-color-scheme:dark){:root{--bg:#0b0f17;--card:#171b26;--alt:#1c2130;--text:#e5e7eb;--sub:#94a3b8;--line:#2d3444;--accent:#60a5fa;--accent-bg:#1e3a5f;--pos:#34d399;--neg:#f87171;--warn-bg:#3a2e12;--warn:#fcd34d}}
*{box-sizing:border-box}body{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:0;background:var(--bg);color:var(--text);line-height:1.5}
header{display:flex;justify-content:space-between;align-items:center;padding:14px 24px;border-bottom:1px solid var(--line)}header a{color:var(--accent);text-decoration:none;font-weight:600}
main{max-width:1200px;margin:0 auto;padding:16px 24px 48px}.tab-input{display:none}.tab-labels{display:flex;gap:8px;margin:12px 0}
.tab-labels label{padding:8px 16px;border-radius:999px;cursor:pointer;color:var(--sub);font-weight:600}.panel{display:none}
#t1:checked~.tab-labels [for=t1],#t2:checked~.tab-labels [for=t2],#t3:checked~.tab-labels [for=t3],#t4:checked~.tab-labels [for=t4]{background:var(--accent-bg);color:var(--accent)}
#t1:checked~#p1,#t2:checked~#p2,#t3:checked~#p3,#t4:checked~#p4{display:block}
section{background:var(--card);border-radius:12px;padding:20px;margin:16px 0;overflow-x:auto}section h2{margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.grid div{background:var(--alt);border-radius:10px;padding:14px 16px}
.grid span{display:block;color:var(--sub);font-size:13px}.grid b{display:block;font-size:20px;margin-top:4px}
table{width:100%;border-collapse:collapse}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
.pos{color:var(--pos)}.neg{color:var(--neg)}.muted,.table-count{color:var(--sub);font-size:13px}.table-heading{display:flex;justify-content:space-between;align-items:baseline}
.pill{padding:3px 10px;border-radius:999px;background:var(--accent-bg);color:var(--accent);font-size:12px;font-weight:600}.pill.warn{background:var(--warn-bg);color:var(--warn)}
.empty{padding:22px;border:1px dashed var(--line);border-radius:10px;color:var(--sub)}svg{width:100%;height:auto}.review-text{white-space:pre-wrap;font-size:13px;max-height:480px;overflow:auto}
details{margin:8px 0}summary{cursor:pointer;font-weight:600}
"""


def read_ledger(path: str = LEDGER_PATH) -> list[dict[str, str]]:
    try:
        with open(path, encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))
    except OSError:
        return []


def signed(value: float, suffix: str = "%") -> str:
    cls = "pos" if value > 0 else "neg" if value < 0 else ""
    return f"<span class='{cls}'>{value:+.2f}{suffix}</span>"


def max_drawdown_pct(equities: list[float]) -> float:
    peak, worst = equities[0], 0.0
    for value in equities:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def curve_svg(rows: list[dict[str, str]]) -> str:
    if len(rows) < 2:
        return "<div class='empty'>기록이 2일 이상 쌓이면 자산 추이 그래프가 표시됩니다.</div>"
    values = [float(row["return_pct"]) for row in rows]
    low, high = min(values + [0.0]), max(values + [0.0])
    span = (high - low) or 1.0
    width, height, pad = 800, 220, 30
    point = lambda i, v: (pad + i * (width - 2 * pad) / (len(values) - 1), pad + (high - v) * (height - 2 * pad) / span)
    line = " ".join("%.1f,%.1f" % point(i, v) for i, v in enumerate(values))
    zero_y = point(0, 0.0)[1]
    return (f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='SPY 보유 계좌 수익률 추이'>"
            f"<line x1='{pad}' x2='{width - pad}' y1='{zero_y:.1f}' y2='{zero_y:.1f}' stroke='var(--line)'/>"
            f"<polyline points='{line}' fill='none' stroke='var(--accent)' stroke-width='2'/>"
            f"<text x='{pad}' y='{height - 6}' fill='var(--sub)' font-size='11'>{e(rows[0]['date'])}</text>"
            f"<text x='{width - pad}' y='{height - 6}' fill='var(--sub)' font-size='11' text-anchor='end'>{e(rows[-1]['date'])}</text>"
            f"<text x='{pad}' y='14' fill='var(--sub)' font-size='11'>{high:+.1f}% ~ {low:+.1f}%</text></svg>")


def fmt_day(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def summary_panel(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<section><h2>SPY 보유 계좌</h2><div class='empty'>장부(data/us_spy_hold.csv)가 아직 없습니다.</div></section>"
    last = rows[-1]
    equity = float(last["equity_usd"])
    cards = [
        ("평가금", f"${equity:,.2f}"), ("수익률", signed(float(last["return_pct"]))),
        ("최대낙폭", signed(max_drawdown_pct([float(r["equity_usd"]) for r in rows]))),
        ("보유", f"SPY {last['shares']}주 · 현금 ${float(last['cash_usd']):,.2f}"), ("기준일", fmt_day(last["date"])),
        ("기록 일수", f"{len(rows)}일 (시작 {fmt_day(rows[0]['date'])}, 원금 ${CAPITAL_USD:,})"),
    ]
    grid = "".join(f"<div><span>{e(label)}</span><b>{value if label in ('수익률', '최대낙폭') else e(value)}</b></div>" for label, value in cards)
    return (f"<section><div class='table-heading'><h2>SPY 보유 계좌 (대조군)</h2><span class='table-count'>가격 수익률만, 배당 제외 · 달러</span></div>"
            f"<div class='grid'>{grid}</div></section>" + market_section())


def market_section() -> str:
    market = _read_us_cache()
    market = [row for row in market if str(row.get("symbol", "")).startswith(".")]
    if not market:
        return ""
    day = max(row["market_date"] for row in market)
    items = "".join(f"<li>{e(us_row_text(row))}</li>" for row in market)
    return f"<section><div class='table-heading'><h2>미국 시황</h2><span class='table-count'>{e(day)} 마감</span></div><ul>{items}</ul></section>"


def account_panel(rows: list[dict[str, str]]) -> str:
    recent = [{"date": fmt_day(r["date"]), "spy_close": r["spy_close"], "equity_usd": f"{float(r['equity_usd']):,.2f}",
               "return_pct": f"{float(r['return_pct']):+.2f}%"} for r in reversed(rows[-30:])]
    head = "".join(f"<th>{e(h)}</th>" for h in ("날짜", "SPY 종가", "평가금($)", "수익률"))
    body = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in r.values()) + "</tr>" for r in recent)
    return (f"<section><h2>자산 추이</h2>{curve_svg(rows)}</section>"
            f"<section><div class='table-heading'><h2>일별 기록</h2><span class='table-count'>최근 {len(recent)}일</span></div>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>"
            "<section><h2>전략 계좌</h2><div class='empty'>미국 전략 가상계좌는 아직 없습니다. 만들면 이곳에 SPY 대조군과 함께 표시합니다.</div></section>")


def research_panel(directory: str = RESEARCH_DIR) -> str:
    try:
        with open(os.path.join(directory, "predictions.csv"), encoding="utf-8-sig") as file:
            predictions = [row for row in csv.DictReader(file) if row.get("slug", "").startswith("us_")]
    except OSError:
        predictions = []
    try:
        names = sorted((n for n in os.listdir(directory) if n.endswith(".md") and "-us_" in n), reverse=True)
    except OSError:
        names = []
    if not predictions and not names:
        return "<section><h2>미국 연구</h2><div class='empty'>미국 연구 결과가 아직 없습니다.</div></section>"
    pred_rows = "".join(
        f"<tr><td>{e(r['date'])}</td><td>{e(r['slug'])}</td><td>{e(r['predicted_verdict'])}</td><td>{e(r['actual_verdict'])}</td>"
        f"<td>{'적중' if r.get('hit') == '1' else '빗나감'}</td><td>{e(r.get('p_evidence', ''))}</td></tr>" for r in reversed(predictions))
    bodies = "".join(f"<details><summary>{e(n[:-3])}</summary><pre class='review-text'>{e(read_text(os.path.join(directory, n)) or '')}</pre></details>"
                     for n in names)
    head = "".join(f"<th>{e(h)}</th>" for h in ("날짜", "연구", "예측", "결과", "적중", "p"))
    return (f"<section><h2>사전 등록 연구</h2><table><thead><tr>{head}</tr></thead><tbody>{pred_rows}</tbody></table></section>"
            f"<section><h2>보고서 전문</h2>{bodies}</section>")


def status_panel(rows: list[dict[str, str]]) -> str:
    items = []
    if rows:
        age = (date.today() - datetime.strptime(rows[-1]["date"], "%Y%m%d").date()).days
        pill = "<span class='pill warn'>갱신 지연</span>" if age > STALE_DAYS else "<span class='pill'>정상</span>"
        items.append(("SPY 장부 마지막 행", f"{fmt_day(rows[-1]['date'])} ({age}일 전) {pill}"))
    else:
        items.append(("SPY 장부", "<span class='pill warn'>파일 없음</span>"))
    try:
        collected = datetime.fromtimestamp(os.path.getmtime(UNIVERSE_PATH)).strftime("%Y-%m-%d")
        with open(UNIVERSE_PATH, encoding="utf-8") as file:
            items.append(("백테스트 종목군", f"{sum(1 for _ in file) - 1}종목, 수집 {collected} (현재 시가총액 상위, 생존편향 있음)"))
    except OSError:
        items.append(("백테스트 종목군", "수집 전"))
    body = "".join(f"<tr><th>{e(k)}</th><td>{v if '<span' in v else e(v)}</td></tr>" for k, v in items)
    return (f"<section><h2>데이터 상태</h2><table><tbody>{body}</tbody></table>"
            "<p class='muted'>장부는 매일 08:30 아침 시황 작업이 끝난 뒤 갱신됩니다. 미국 휴장일에는 행이 늘지 않습니다.</p></section>")


def render_us() -> str:
    rows = read_ledger()
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>stockAlarm · 미국</title><style>{CSS}</style></head><body>
<header><b>stockAlarm · 미국</b><span><span class="muted">생성 {e(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</span> &nbsp; <a href="/">← 국내 대시보드</a></span></header>
<main><div class="tabs">
<input class="tab-input" id="t1" name="t" type="radio" checked><input class="tab-input" id="t2" name="t" type="radio">
<input class="tab-input" id="t3" name="t" type="radio"><input class="tab-input" id="t4" name="t" type="radio">
<div class="tab-labels"><label for="t1">요약</label><label for="t2">계좌</label><label for="t3">연구</label><label for="t4">데이터 상태</label></div>
<div class="panel" id="p1">{summary_panel(rows)}</div><div class="panel" id="p2">{account_panel(rows)}</div>
<div class="panel" id="p3">{research_panel()}</div><div class="panel" id="p4">{status_panel(rows)}</div>
</div></main></body></html>"""
