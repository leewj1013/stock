from unittest.mock import patch

from stock_alarm import us_dashboard
from stock_alarm.us_strategy_account import ACCOUNTS

A100, A300 = ACCOUNTS
ROWS = [
    {"date": "20261005", "spy_close": "774.83", "shares": "129", "cash_usd": "46.93", "equity_usd": "100000.0", "return_pct": "0.0"},
    {"date": "20261006", "spy_close": "790.0", "shares": "129", "cash_usd": "46.93", "equity_usd": "101960.93", "return_pct": "1.9609"},
    {"date": "20261007", "spy_close": "795.0", "shares": "129", "cash_usd": "46.93", "equity_usd": "102605.93", "return_pct": "2.6059"},
]


def equity(*points):
    return [{"date": d, "equity_usd": f"{v:.2f}", "cash_usd": "90000.00", "invested_weight": "0.1", "position_count": "1",
             "return_pct": f"{(v / 100000 - 1) * 100:.4f}", "pending": p} for d, v, p in points]


OK = {"state": "ok", "last_day": "2026-10-07", "updated_at": "2026-10-08T08:40:00", "stale_holdings": [], "refresh_failures": []}
HOLD = [{"ticker": "PG", "name": "P&G", "shares": "60", "entry_date": "2026-10-07", "entry_usd": "150.00", "last_usd": "148.50", "return_pct": "-1.00"}]


def strategy(account, eq, status=None, holdings=(), names=None):
    return {"account": account, "equity": eq, "holdings": list(holdings), "trades": [], "status": status or {}, "names": names or {}}


def render(rows, strategies):
    with patch.object(us_dashboard, "read_ledger", return_value=rows), patch.object(us_dashboard, "_read_us_cache", return_value=[]), \
            patch.object(us_dashboard, "read_strategy", side_effect=lambda a: next(s for s in strategies if s["account"].key == a.key)):
        return us_dashboard.render_us()


def test_render_with_one_account_shows_cards_and_one_chart():
    page = render(ROWS, [strategy(A100, equity(("2026-10-05", 100000, "PG"), ("2026-10-06", 99000, ""), ("2026-10-07", 99500, "")), OK, HOLD),
                         strategy(A300, [], {"state": "waiting", "updated_at": "2026-10-08T08:40:00"})])
    assert "102,605.93" in page and "SPY 129주" in page  # SPY account (latest row)
    assert A100.label in page and A300.label in page and "체결 대기" in page and "PG" in page
    assert page.count("<polyline") == 2  # first account + SPY; the second account has no ledger yet


def test_render_adds_a_comparison_chart_once_the_second_account_has_two_days():
    page = render(ROWS, [strategy(A100, equity(("2026-10-05", 100000, ""), ("2026-10-06", 99000, ""), ("2026-10-07", 99500, "")), OK),
                         strategy(A300, equity(("2026-10-06", 100000, ""), ("2026-10-07", 100400, "")), OK)])
    assert page.count("<polyline") == 3  # both strategy accounts + SPY on one common-period chart
    assert "공통 기간" in page


def test_render_without_any_ledger_degrades_to_empty_states():
    page = render([], [strategy(A100, []), strategy(A300, [])])
    assert "아직 없습니다" in page and "파일 없음" in page


def test_waiting_account_is_not_flagged_as_a_problem_but_diverged_is():
    waiting = strategy(A300, [], {"state": "waiting", "updated_at": "2026-10-08T08:40:00"})
    fine = render(ROWS, [strategy(A100, equity(("2026-10-05", 100000, "")), OK), waiting])
    bad = render(ROWS, [strategy(A100, equity(("2026-10-05", 100000, "")), {**OK, "state": "diverged", "detail": ["x"]}), waiting])
    assert "시작 전" in fine and "기록 불일치" in bad and "기록 불일치" not in fine


def test_max_drawdown():
    assert round(us_dashboard.max_drawdown_pct([100, 120, 90, 110]), 2) == -25.0


def test_render_uses_the_shared_domestic_frame():
    page = render(ROWS, [strategy(A100, equity(("2026-10-05", 100000, ""))), strategy(A300, [])])
    for marker in ("dashboard-header", "theme-toggle", 'class="tab-labels"', "stocks-panel", "const themeKey"):
        assert marker in page
    assert '<h1 class="sr-only">미국주식 알림 대시보드</h1>' in page
    assert [label for label in ("홈", "성과 추적", "가상 트레이더", "전략 연구", "시스템 관리") if label in page] == [
        "홈", "성과 추적", "가상 트레이더", "전략 연구", "시스템 관리"]
    assert page.index('id="stocks-panel"') < page.index('id="tracking-panel"') < page.index('id="trader-panel"')


def test_account_table_prefers_korean_name_and_keeps_ticker_visible():
    page = render(ROWS, [strategy(A100, equity(("2026-10-07", 99500, "PG")), OK, HOLD, {"PG": "프록터 앤드 갬블"}),
                         strategy(A300, [])])
    assert "프록터 앤드 갬블 (PG)" in page


def test_account_tab_shows_spy_balance_and_unstarted_strategies_without_invented_returns():
    page = render(ROWS, [strategy(A100, []), strategy(A300, [])])
    account_panel = page.split('id="trader-panel"', 1)[1].split('id="research-panel"', 1)[0]
    assert "계좌별 잔고·수익률" in account_panel
    assert "$102,605.93" in account_panel and "+2.61%" in account_panel
    assert account_panel.count("$100,000.00") == 3
    assert account_panel.count("시작 전") >= 2


def test_performance_history_is_in_tracking_tab_and_account_balances_stay_in_trader_tab():
    page = render(ROWS, [strategy(A100, equity(("2026-10-05", 100000, ""), ("2026-10-06", 99000, "")), OK),
                         strategy(A300, [])])
    tracking = page.split('id="tracking-panel"', 1)[1].split('id="trader-panel"', 1)[0]
    account = page.split('id="trader-panel"', 1)[1].split('id="research-panel"', 1)[0]
    assert "자산 추이" in tracking and "SPY 보유 일별 기록" in tracking and "일별 기록" in tracking
    assert "계좌별 잔고·수익률" in account and "설정 원금" in account
    assert "계좌별 잔고·수익률" not in tracking and "자산 추이" not in account
