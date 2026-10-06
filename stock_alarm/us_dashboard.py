"""US dashboard page (served at /us). Read-only view of the US control account, market and research.

Uses the same frame (CSS, header, sidebar tabs, theme script) as the domestic dashboard via dashboard_shell,
but is built separately so a failure here cannot blank the domestic page.
"""
import csv
import os
from datetime import date, datetime

from .dashboard import dashboard_logo_data_uri, details, display_label, read_text
from .dashboard_shell import PAGE_CSS, SHELL_JS, e, shell_header, shell_tabs
from .market_summary import _read_us_cache, us_row_text
from .us_spy_hold import CAPITAL_USD, LEDGER_PATH

RESEARCH_DIR = "reports/research"
UNIVERSE_PATH = "data/backtest_us/universe.csv"
STALE_DAYS = 5  # weekend + a US holiday still leaves a fresh row within 5 days
DOMESTIC_LINK = ('<a href="/" style="align-self:center;margin-left:auto;margin-right:16px;font-weight:600">'
                 "&larr; 국내</a>")
TABS = [("tab-stocks", "home", "요약", "stocks-panel"), ("tab-trader", "chart", "계좌", "trader-panel"),
        ("tab-research", "flask", "전략 연구", "research-panel"), ("tab-system", "settings", "데이터 상태", "system-panel")]


def read_ledger(path: str = LEDGER_PATH) -> list[dict[str, str]]:
    try:
        with open(path, encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))
    except OSError:
        return []


def signed(value: float, suffix: str = "%") -> str:
    cls = "pos" if value > 0 else "neg" if value < 0 else "zero"
    return f"<em class='{cls}' style='font-style:normal'>{value:+.2f}{suffix}</em>"  # not <span>: card CSS shrinks spans


def max_drawdown_pct(equities: list[float]) -> float:
    peak, worst = equities[0], 0.0
    for value in equities:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def fmt_day(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def heading(title: str, note: str, pill: str = "") -> str:
    return f"<div class='home-heading'><div><h2>{e(title)}</h2><p class='muted'>{e(note)}</p></div>{pill}</div>"


def empty(title: str, message: str) -> str:
    return (f"<section class='empty-section'><h2>{e(title)}</h2><div class='empty-state'><b>현재 항목이 없습니다</b>"
            f"<span>{e(message)}</span></div></section>")


def simple_table(title: str, columns: list[str], rows: list[list[str]], count: str = "", num_from: int = 1) -> str:
    num = " class='num'"
    head = "".join(f"<th{num if i >= num_from else ''}>{e(c)}</th>" for i, c in enumerate(columns))
    body = "".join("<tr>" + "".join(f"<td{num if i >= num_from else ''}>{v}</td>" for i, v in enumerate(row)) + "</tr>"
                   for row in rows)
    note = f"<span class='table-count'>{e(count)}</span>" if count else ""
    return (f"<section><div class='table-heading'><h2>{e(title)}</h2>{note}</div>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>")


def curve_section(rows: list[dict[str, str]]) -> str:
    if len(rows) < 2:
        return empty("자산 추이", "기록이 2일 이상 쌓이면 그래프가 표시됩니다.")
    values = [float(row["return_pct"]) for row in rows]
    low, high = min(values + [0.0]), max(values + [0.0])
    span = (high - low) or 1.0
    width, height, left, top, bottom = 960.0, 200.0, 52.0, 14.0, 26.0
    x_of = lambda i: left + (width - left - 12) * (i / (len(values) - 1))
    y_of = lambda v: top + (height - top - bottom) * (1 - (v - low) / span)
    points = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in enumerate(values))
    grid = "".join(
        f"<line x1='{left:.0f}' y1='{y_of(v):.1f}' x2='{width - 12:.0f}' y2='{y_of(v):.1f}' class='curve-grid'/>"
        f"<text x='6' y='{y_of(v) + 4:.1f}' class='curve-axis'>{v:+.1f}%</text>"
        for v in dict.fromkeys((high, (low + high) / 2, low)))
    labels = (f"<text x='{left:.0f}' y='{height - 6:.0f}' class='curve-axis'>{e(fmt_day(rows[0]['date'])[5:])}</text>"
              f"<text x='{width - 70:.0f}' y='{height - 6:.0f}' class='curve-axis'>{e(fmt_day(rows[-1]['date'])[5:])}</text>")
    sign = "pos" if values[-1] > 0 else "neg" if values[-1] < 0 else "zero"
    legend = f"<span class='curve-legend-item'><i style='background:var(--accent)'></i>SPY 보유 <b class='{sign}'>{values[-1]:+.2f}%</b></span>"
    return ("<section class='curve-card'><div class='table-heading'><h2>자산 추이</h2>"
            f"<span class='table-count'>수익률 · 최근 {len(rows)}일</span></div>"
            f"<svg viewBox='0 0 {width:.0f} {height:.0f}' class='equity-curve' role='img' aria-label='SPY 보유 계좌 수익률 추이'>"
            f"{grid}<polyline fill='none' stroke='var(--accent)' stroke-width='2' points='{points}'/>{labels}</svg>"
            f"<div class='curve-legend'>{legend}</div></section>")


def market_section() -> str:
    market = [row for row in _read_us_cache() if str(row.get("symbol", "")).startswith(".")]
    if not market:
        return ""
    day = max(row["market_date"] for row in market)
    cards = "".join(f"<div><span>{e(row['name'])}</span><b>{e(us_row_text(row).replace(row['name'], '').strip())}</b></div>"
                    for row in market)
    return (f"<section><div class='table-heading'><h2>미국 시황</h2><span class='table-count'>{e(day)} 마감</span></div>"
            f"<div class='operation-grid'>{cards}</div></section>")


def summary_panel(rows: list[dict[str, str]]) -> str:
    title = heading("미국 투자 현황", "SPY 보유 계좌(대조군)와 미국 시황입니다. 달러 기준, 가격 수익률만 반영합니다(배당 제외).")
    if not rows:
        return title + empty("SPY 보유 계좌", "장부(data/us_spy_hold.csv)가 아직 없습니다.") + market_section()
    last = rows[-1]
    cards = [
        ("평가금", e(f"${float(last['equity_usd']):,.2f}")), ("수익률", signed(float(last["return_pct"]))),
        ("최대낙폭", signed(max_drawdown_pct([float(r["equity_usd"]) for r in rows]))),
        ("보유", e(f"SPY {last['shares']}주 · 현금 ${float(last['cash_usd']):,.2f}")), ("기준일", e(fmt_day(last["date"]))),
        ("기록", e(f"{len(rows)}일 · 시작 {fmt_day(rows[0]['date'])} · 원금 ${CAPITAL_USD:,}")),
    ]
    grid = "".join(f"<div><span>{e(label)}</span><b>{value}</b></div>" for label, value in cards)
    return (title + "<section><div class='table-heading'><h2>SPY 보유 계좌 (대조군)</h2></div>"
            f"<div class='operation-grid'>{grid}</div></section>" + market_section())


def account_panel(rows: list[dict[str, str]]) -> str:
    recent = [[e(fmt_day(r["date"])), e(r["spy_close"]), e(f"{float(r['equity_usd']):,.2f}"), signed(float(r["return_pct"]))]
              for r in reversed(rows[-30:])]
    table = (simple_table("일별 기록", ["날짜", "SPY 종가", "평가금($)", "수익률"], recent, f"최근 {len(recent)}일")
             if recent else empty("일별 기록", "장부가 아직 없습니다."))
    return (heading("미국 가상 계좌", "SPY 보유 계좌만 기록 중입니다. 전략 계좌가 생기면 같은 그래프에서 비교합니다.")
            + curve_section(rows) + table + empty("전략 계좌", "미국 전략 가상계좌는 아직 만들지 않았습니다."))


def research_panel(directory: str = RESEARCH_DIR) -> str:
    title = heading("미국 전략 연구", "한국에서 검증한 규칙이 미국에서도 통하는지 사전 등록 방식으로 확인합니다.")
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
        return title + empty("연구 결과", "미국 연구 결과가 아직 없습니다.")
    rows = [[e(r["date"]), e(r["slug"]), e(r["predicted_verdict"]), e(r["actual_verdict"]),
             "적중" if r.get("hit") == "1" else "빗나감", e(r.get("p_evidence", ""))] for r in reversed(predictions)]
    bodies = "".join(details(n[:-3], f"<pre class='review-text'>{e(read_text(os.path.join(directory, n)) or '')}</pre>") for n in names)
    return (title + simple_table("연구 결과 (사전 등록)", ["날짜", "연구", "예측", "결과", "적중", "p"], rows, num_from=99)
            + (f"<section><h2>연구 보고서 전문</h2>{bodies}</section>" if bodies else ""))


def status_panel(rows: list[dict[str, str]]) -> str:
    items = []
    if rows:
        age = (date.today() - datetime.strptime(rows[-1]["date"], "%Y%m%d").date()).days
        stale = age > STALE_DAYS
        pill = "<span class='status-pill pill-warn'>갱신 지연</span>" if stale else "<span class='status-pill pill-accent'>정상</span>"
        items.append(["SPY 장부 마지막 행", f"{e(fmt_day(rows[-1]['date']))} ({age}일 전) {pill}"])
    else:
        stale = True
        items.append(["SPY 장부", "<span class='status-pill pill-warn'>파일 없음</span>"])
    try:
        collected = datetime.fromtimestamp(os.path.getmtime(UNIVERSE_PATH)).strftime("%Y-%m-%d")
        with open(UNIVERSE_PATH, encoding="utf-8") as file:
            items.append(["백테스트 종목군", e(f"{sum(1 for _ in file) - 1}종목 · 수집 {collected} · 현재 시가총액 상위(생존편향 있음)")])
    except OSError:
        items.append(["백테스트 종목군", "수집 전"])
    pill = f"<span class='system-pill {'bad' if stale else 'ok'}'>{'확인 필요' if stale else '모든 데이터 정상'}</span>"
    return (heading("데이터 상태", "미국 장부와 수집 데이터의 최신 여부입니다. 장부는 매일 08:30 아침 시황 작업 뒤에 갱신됩니다.", pill)
            + simple_table("데이터", ["항목", "상태"], items, num_from=99))


def render_us() -> str:
    rows = read_ledger()
    panels = {"stocks-panel": summary_panel(rows), "trader-panel": account_panel(rows),
              "research-panel": research_panel(), "system-panel": status_panel(rows)}
    generated = f"{e(display_label('generated'))} {e(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}"
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>stockAlarm · 미국</title>
<style>
{PAGE_CSS}
</style>
</head>
<body>
{shell_header(dashboard_logo_data_uri(), generated, DOMESTIC_LINK)}
{shell_tabs(TABS, panels)}
<script>
{SHELL_JS}
</script>
</body>
</html>"""
