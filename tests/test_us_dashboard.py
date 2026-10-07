from unittest.mock import patch

from stock_alarm import us_dashboard

ROWS = [
    {"date": "20261005", "spy_close": "774.83", "shares": "129", "cash_usd": "46.93", "equity_usd": "100000.0", "return_pct": "0.0"},
    {"date": "20261006", "spy_close": "790.0", "shares": "129", "cash_usd": "46.93", "equity_usd": "101960.93", "return_pct": "1.9609"},
]
NO_STRATEGY = {"equity": [], "holdings": [], "trades": [], "status": {}}
STRATEGY = {
    "equity": [
        {"date": "2026-10-05", "equity_usd": "100000.00", "cash_usd": "100000.00", "invested_weight": "0", "position_count": "0", "return_pct": "0.0", "pending": "PG"},
        {"date": "2026-10-06", "equity_usd": "99000.00", "cash_usd": "90000.00", "invested_weight": "0.1", "position_count": "1", "return_pct": "-1.0", "pending": ""},
    ],
    "holdings": [{"ticker": "PG", "name": "P&G", "shares": "60", "entry_date": "2026-10-07", "entry_usd": "150.00", "last_usd": "148.50", "return_pct": "-1.00"}],
    "trades": [],
    "status": {"state": "ok", "last_day": "2026-10-07", "updated_at": "2026-10-08T08:40:00", "stale_holdings": [], "refresh_failures": []},
}


def render(rows, strategy):
    with patch.object(us_dashboard, "read_ledger", return_value=rows), patch.object(us_dashboard, "read_strategy", return_value=strategy), \
            patch.object(us_dashboard, "_read_us_cache", return_value=[]):
        return us_dashboard.render_us()


def test_render_with_ledgers_shows_both_accounts_and_one_chart():
    page = render(ROWS, STRATEGY)
    assert "101,960.93" in page and "SPY 129주" in page            # SPY account
    assert "미국 전략 계좌" in page and "체결 대기" in page and "PG" in page  # strategy account
    assert page.count("<polyline") == 2                            # strategy and SPY on the same chart


def test_render_without_any_ledger_degrades_to_empty_states():
    page = render([], NO_STRATEGY)
    assert "아직 없습니다" in page and "파일 없음" in page


def test_render_flags_a_diverged_strategy_account():
    bad = {**STRATEGY, "status": {**STRATEGY["status"], "state": "diverged", "detail": ["x"]}}
    page = render(ROWS, bad)
    assert "기록 불일치" in page and "확인 필요" in page


def test_max_drawdown():
    assert round(us_dashboard.max_drawdown_pct([100, 120, 90, 110]), 2) == -25.0


def test_render_uses_the_shared_domestic_frame():
    page = render(ROWS, STRATEGY)
    for marker in ("dashboard-header", "theme-toggle", 'class="tab-labels"', "stocks-panel", "const themeKey"):
        assert marker in page
