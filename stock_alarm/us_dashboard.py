"""US dashboard page (served at /us). Read-only view of the US strategy accounts, SPY control account, market and research.

Uses the same frame (CSS, header, sidebar tabs, theme script) as the domestic dashboard via dashboard_shell,
but is built separately so a failure here cannot blank the domestic page.
"""
import csv
import json
import os
from datetime import date, datetime

from .dashboard import dashboard_logo_data_uri, details, display_label, read_text
from .dashboard_shell import PAGE_CSS, SHELL_JS, e, shell_header, shell_tabs
from .market_summary import _read_us_cache, us_row_text
from .us_spy_hold import CAPITAL_USD, LEDGER_PATH
from .us_strategy_account import ACCOUNTS

RESEARCH_DIR = "reports/research"
UNIVERSE_PATH = "data/backtest_us/universe.csv"
STALE_DAYS = 5  # weekend + a US holiday still leaves a fresh row within 5 days
DOMESTIC_LINK = ('<a href="/" style="align-self:center;margin-left:auto;margin-right:16px;font-weight:600">'
                 "&larr; 국내</a>")
TABS = [("tab-stocks", "home", "요약", "stocks-panel"), ("tab-trader", "chart", "계좌", "trader-panel"),
        ("tab-research", "flask", "전략 연구", "research-panel"), ("tab-system", "settings", "데이터 상태", "system-panel")]
STATE_LABELS = {"ok": "정상", "waiting": "시작 전", "diverged": "기록 불일치(갱신 중단)", "config_changed": "설정 변경됨(갱신 중단)",
                "data_problem": "수집 데이터 이상(갱신 중단)", "error": "갱신 오류"}
COLORS = {"100": "var(--accent)", "300": "#a78bfa"}
SPY_COLOR = "#f59e0b"


def read_csv(path) -> list[dict[str, str]]:
    try:
        with open(path, encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))
    except OSError:
        return []


def read_ledger(path: str = LEDGER_PATH) -> list[dict[str, str]]:
    return read_csv(path)


def read_strategy(account) -> dict:
    try:
        with open(account.path("status.json"), encoding="utf-8") as file:
            status = json.load(file)
    except (OSError, ValueError):
        status = {}
    return {"account": account, "equity": read_csv(account.path("equity.csv")), "holdings": read_csv(account.path("holdings.csv")),
            "trades": read_csv(account.path("trades.csv")), "status": status}


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


def curve_section(series: list[dict], title: str = "자산 추이") -> str:
    """series: [{"label", "color", "points": [(iso_date, return_pct), ...]}], all on the same dates."""
    series = [s for s in series if len(s["points"]) >= 2]
    if not series:
        return empty(title, "기록이 2일 이상 쌓이면 그래프가 표시됩니다.")
    days = [d for d, _ in series[0]["points"]]
    every = [v for s in series for _, v in s["points"]] + [0.0]
    low, high = min(every), max(every)
    span = (high - low) or 1.0
    width, height, left, top, bottom = 960.0, 200.0, 52.0, 14.0, 26.0
    x_of = lambda i: left + (width - left - 12) * (i / (len(days) - 1))
    y_of = lambda v: top + (height - top - bottom) * (1 - (v - low) / span)
    grid = "".join(
        f"<line x1='{left:.0f}' y1='{y_of(v):.1f}' x2='{width - 12:.0f}' y2='{y_of(v):.1f}' class='curve-grid'/>"
        f"<text x='6' y='{y_of(v) + 4:.1f}' class='curve-axis'>{v:+.1f}%</text>"
        for v in dict.fromkeys((high, (low + high) / 2, low)))
    lines, legend = [], []
    for s in series:
        points = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, (_d, v) in enumerate(s["points"]))
        lines.append(f"<polyline fill='none' stroke='{s['color']}' stroke-width='2' points='{points}'/>")
        last = s["points"][-1][1]
        sign = "pos" if last > 0 else "neg" if last < 0 else "zero"
        legend.append(f"<span class='curve-legend-item'><i style='background:{s['color']}'></i>{e(s['label'])} <b class='{sign}'>{last:+.2f}%</b></span>")
    labels = (f"<text x='{left:.0f}' y='{height - 6:.0f}' class='curve-axis'>{e(days[0][5:])}</text>"
              f"<text x='{width - 70:.0f}' y='{height - 6:.0f}' class='curve-axis'>{e(days[-1][5:])}</text>")
    return (f"<section class='curve-card'><div class='table-heading'><h2>{e(title)}</h2>"
            f"<span class='table-count'>수익률 · {e(days[0])}부터 {len(days)}일</span></div>"
            f"<svg viewBox='0 0 {width:.0f} {height:.0f}' class='equity-curve' role='img' aria-label='계좌별 수익률 추이'>"
            f"{grid}{''.join(lines)}{labels}</svg><div class='curve-legend'>{''.join(legend)}</div></section>")


def chart_series(rows: list[dict[str, str]], strategies: list[dict]) -> list[dict]:
    """The given accounts and SPY, rebased to their first common day so they all start at 0%."""
    spy = {fmt_day(r["date"]): float(r["equity_usd"]) for r in rows}
    mine = [(s["account"], {r["date"]: float(r["equity_usd"]) for r in s["equity"]}) for s in strategies if s["equity"]]
    if not mine:
        return []
    days = sorted(set(spy).intersection(*[set(m) for _, m in mine]))
    if not days:
        return []
    base = days[0]
    series = [{"label": a.label, "color": COLORS.get(a.key, "var(--accent)"), "points": [(d, (m[d] / m[base] - 1) * 100) for d in days]}
              for a, m in mine]
    return series + [{"label": "SPY 보유", "color": SPY_COLOR, "points": [(d, (spy[d] / spy[base] - 1) * 100) for d in days]}]


def market_section() -> str:
    market = [row for row in _read_us_cache() if str(row.get("symbol", "")).startswith(".")]
    if not market:
        return ""
    day = max(row["market_date"] for row in market)
    cards = "".join(f"<div><span>{e(row['name'])}</span><b>{e(us_row_text(row).replace(row['name'], '').strip())}</b></div>"
                    for row in market)
    return (f"<section><div class='table-heading'><h2>미국 시황</h2><span class='table-count'>{e(day)} 마감</span></div>"
            f"<div class='operation-grid'>{cards}</div></section>")


def strategy_cards(strategy: dict, spy_rows: list[dict[str, str]]) -> str:
    label, eq = strategy["account"].label, strategy["equity"]
    if not eq:
        return empty(label, f"장부({strategy['account'].path('equity.csv')})가 아직 없습니다. 첫 갱신 뒤에 표시됩니다.")
    last = eq[-1]
    spy = {fmt_day(r["date"]): float(r["equity_usd"]) for r in spy_rows}
    versus = ""
    if eq[0]["date"] in spy and last["date"] in spy:
        versus = signed(float(last["return_pct"]) - (spy[last["date"]] / spy[eq[0]["date"]] - 1) * 100, "%p")
    cards = [("평가금", e(f"${float(last['equity_usd']):,.2f}")), ("수익률", signed(float(last["return_pct"]))),
             ("SPY 대비", versus or "-"), ("보유", e(f"{last['position_count']}종목 · 현금 ${float(last['cash_usd']):,.2f}")),
             ("체결 대기", e(last["pending"] or "없음")), ("기준일", e(last["date"]))]
    grid = "".join(f"<div><span>{e(name)}</span><b>{value}</b></div>" for name, value in cards)
    return (f"<section><div class='table-heading'><h2>{e(label)}</h2>"
            f"<span class='table-count'>시작 {e(eq[0]['date'])} · 원금 ${CAPITAL_USD:,}</span></div>"
            f"<div class='operation-grid'>{grid}</div></section>")


def summary_panel(rows: list[dict[str, str]], strategies: list[dict]) -> str:
    out = (heading("미국 투자 현황", "미국 전략 계좌들과 SPY 보유 계좌(대조군), 미국 시황입니다. 달러 기준, 가격 수익률만 반영합니다(배당 제외).")
           + "".join(strategy_cards(s, rows) for s in strategies))
    if not rows:
        return out + empty("SPY 보유 계좌", "장부(data/us_spy_hold.csv)가 아직 없습니다.") + market_section()
    last = rows[-1]
    cards = [
        ("평가금", e(f"${float(last['equity_usd']):,.2f}")), ("수익률", signed(float(last["return_pct"]))),
        ("최대낙폭", signed(max_drawdown_pct([float(r["equity_usd"]) for r in rows]))),
        ("보유", e(f"SPY {last['shares']}주 · 현금 ${float(last['cash_usd']):,.2f}")), ("기준일", e(fmt_day(last["date"]))),
        ("기록", e(f"{len(rows)}일 · 시작 {fmt_day(rows[0]['date'])} · 원금 ${CAPITAL_USD:,}")),
    ]
    grid = "".join(f"<div><span>{e(label)}</span><b>{value}</b></div>" for label, value in cards)
    return (out + "<section><div class='table-heading'><h2>SPY 보유 계좌 (대조군)</h2></div>"
            f"<div class='operation-grid'>{grid}</div></section>" + market_section())


def account_tables(strategy: dict) -> str:
    label = strategy["account"].label
    holdings = [[e(r["ticker"]), e(r["shares"]), e(r["entry_date"]), e(r["entry_usd"]), e(r["last_usd"] or "-"),
                 signed(float(r["return_pct"])) if r["return_pct"] else "-"] for r in strategy["holdings"]]
    trades = [[e(r["ticker"]), e(r["entry_date"]), e(r["exit_date"]), e(r["entry_usd"]), e(r["exit_usd"]),
               signed(float(r["return_pct"])), e(r["reason"])] for r in reversed(strategy["trades"][-20:])]
    return ((simple_table(f"보유 종목 · {label}", ["종목", "수량", "매수일", "매수가($)", "현재가($)", "수익률"], holdings)
             if holdings else empty(f"보유 종목 · {label}", "현재 보유 종목이 없습니다."))
            + (simple_table(f"청산 내역 · {label}", ["종목", "매수일", "매도일", "매수가($)", "매도가($)", "수익률", "사유"], trades,
                            f"최근 {len(trades)}건", num_from=3)
               if trades else empty(f"청산 내역 · {label}", "아직 청산된 거래가 없습니다.")))


def account_panel(rows: list[dict[str, str]], strategies: list[dict]) -> str:
    recent = [[e(fmt_day(r["date"])), e(r["spy_close"]), e(f"{float(r['equity_usd']):,.2f}"), signed(float(r["return_pct"]))]
              for r in reversed(rows[-30:])]
    first, others = strategies[0], strategies[1:]
    charts = curve_section(chart_series(rows, [first]), f"자산 추이 · {first['account'].label}")
    if others and all(len(s["equity"]) >= 2 for s in others):  # side-by-side from the later account's start day
        charts += curve_section(chart_series(rows, strategies), "자산 추이 · 계좌 비교 (늦게 시작한 계좌 기준)")
    return (heading("미국 가상 계좌", "전략 계좌는 한국과 같은 규칙을 일봉으로 재현합니다(마감 후 신호, 다음 날 시가 체결). 계좌는 종목 수만 다릅니다.")
            + charts + "".join(strategy_cards(s, rows) + account_tables(s) for s in strategies)
            + (simple_table("SPY 보유 일별 기록", ["날짜", "SPY 종가", "평가금($)", "수익률"], recent, f"최근 {len(recent)}일")
               if recent else empty("SPY 보유 일별 기록", "장부가 아직 없습니다.")))


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


def pill(text: str, warn: bool) -> str:
    return f"<span class='status-pill {'pill-warn' if warn else 'pill-accent'}'>{e(text)}</span>"


def status_panel(rows: list[dict[str, str]], strategies: list[dict]) -> str:
    items, problem = [], False
    if rows:
        age = (date.today() - datetime.strptime(rows[-1]["date"], "%Y%m%d").date()).days
        late = age > STALE_DAYS
        problem |= late
        items.append(["SPY 장부 마지막 행", f"{e(fmt_day(rows[-1]['date']))} ({age}일 전) {pill('갱신 지연' if late else '정상', late)}"])
    else:
        problem = True
        items.append(["SPY 장부", pill("파일 없음", True)])
    for strategy in strategies:
        account, status = strategy["account"], strategy["status"]
        if status.get("state"):
            age = (date.today() - datetime.fromisoformat(status["updated_at"]).date()).days
            bad = status["state"] not in ("ok", "waiting") or age > STALE_DAYS or bool(status.get("stale_holdings"))
            problem |= bad
            items.append([account.label, f"{e(status.get('last_day', '-'))} 기준 · 갱신 {e(status['updated_at'][:16])} "
                                         f"{pill(STATE_LABELS.get(status['state'], status['state']), bad)}"])
            if status.get("detail"):
                items.append([f"{account.label} 상세", e(str(status["detail"])[:300])])
            if status.get("stale_holdings"):
                items.append([f"{account.label} 시세 없는 보유", e(", ".join(status["stale_holdings"]) + " (진입가로 평가 중)")])
            if status.get("anomalies"):
                items.append([f"{account.label} 급변동(참고)", e(", ".join(f"{a['ticker']} {a['date']} {a['move_pct']:+.1f}%" for a in status["anomalies"][:8]))])
            if status.get("refresh_failures"):
                items.append([f"{account.label} 수집 실패", e(", ".join(str(item[0]) for item in status["refresh_failures"]))])
        else:
            problem |= bool(strategy["equity"])  # no status but a ledger means the status file was lost
            items.append([account.label, pill("파일 없음", True)])
        try:
            collected = datetime.fromtimestamp(os.path.getmtime(account.path("universe.csv"))).strftime("%Y-%m-%d")
            with open(account.path("universe.csv"), encoding="utf-8") as file:
                items.append([f"{account.label} 종목군", e(f"{sum(1 for _ in file) - 1}종목 · {collected} · 시작 시 고정")])
        except OSError:
            items.append([f"{account.label} 종목군", "수집 전"])
    try:
        collected = datetime.fromtimestamp(os.path.getmtime(UNIVERSE_PATH)).strftime("%Y-%m-%d")
        with open(UNIVERSE_PATH, encoding="utf-8") as file:
            items.append(["백테스트 종목군", e(f"{sum(1 for _ in file) - 1}종목 · {collected} · 현재 시가총액 상위, 생존편향 있음")])
    except OSError:
        items.append(["백테스트 종목군", "수집 전"])
    state_pill = f"<span class='system-pill {'bad' if problem else 'ok'}'>{'확인 필요' if problem else '모든 데이터 정상'}</span>"
    return (heading("데이터 상태", "미국 장부와 수집 데이터의 최신 여부입니다. 장부는 매일 아침 시황 작업 뒤에 갱신됩니다.", state_pill)
            + simple_table("데이터", ["항목", "상태"], items, num_from=99))


def render_us() -> str:
    rows, strategies = read_ledger(), [read_strategy(a) for a in ACCOUNTS]
    panels = {"stocks-panel": summary_panel(rows, strategies), "trader-panel": account_panel(rows, strategies),
              "research-panel": research_panel(), "system-panel": status_panel(rows, strategies)}
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
