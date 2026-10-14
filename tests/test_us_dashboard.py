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


def strategy(account, eq, status=None, holdings=()):
    return {"account": account, "equity": eq, "holdings": list(holdings), "trades": [], "status": status or {}}


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
    assert page.count("<polyline") == 2 + 3  # chart 1: account + SPY; chart 2: both accounts + SPY
    assert "계좌 비교" in page


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
