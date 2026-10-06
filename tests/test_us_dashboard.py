from unittest.mock import patch

from stock_alarm import us_dashboard

ROWS = [
    {"date": "20261005", "spy_close": "774.83", "shares": "129", "cash_usd": "46.93", "equity_usd": "100000.0", "return_pct": "0.0"},
    {"date": "20261006", "spy_close": "790.0", "shares": "129", "cash_usd": "46.93", "equity_usd": "101960.93", "return_pct": "1.9609"},
]


def test_render_with_ledger_shows_account_and_chart():
    with patch.object(us_dashboard, "read_ledger", return_value=ROWS), patch.object(us_dashboard, "_read_us_cache", return_value=[]):
        page = us_dashboard.render_us()
    assert "101,960.93" in page and "<polyline" in page and "SPY 129주" in page


def test_render_without_ledger_degrades_to_empty_states():
    with patch.object(us_dashboard, "read_ledger", return_value=[]), patch.object(us_dashboard, "_read_us_cache", return_value=[]):
        page = us_dashboard.render_us()
    assert "아직 없습니다" in page and "파일 없음" in page


def test_max_drawdown():
    assert round(us_dashboard.max_drawdown_pct([100, 120, 90, 110]), 2) == -25.0


def test_render_uses_the_shared_domestic_frame():
    with patch.object(us_dashboard, "read_ledger", return_value=ROWS), patch.object(us_dashboard, "_read_us_cache", return_value=[]):
        page = us_dashboard.render_us()
    for marker in ("dashboard-header", "theme-toggle", 'class="tab-labels"', "stocks-panel", "const themeKey"):
        assert marker in page
