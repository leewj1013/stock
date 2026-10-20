import csv
from datetime import date
from unittest.mock import patch

from stock_alarm import exit_context as ctx
from stock_alarm.sell_check import SellAlert, check_position, format_message

DAY = date(2026, 10, 21)


def candle(ts, close, volume=100, high=None):
    return {"timestamp": f"{ts}:00.000+09:00", "openPrice": str(close), "highPrice": str(high or close), "lowPrice": str(close),
            "closePrice": str(close), "volume": str(volume), "currency": "KRW"}


def pages(*page_list):
    """candle_fn serving the given pages (newest first) one by one through nextBefore."""
    served = iter(page_list)
    calls = []

    def fn(symbol, interval, count, before):
        calls.append(before)
        page = next(served, [])
        return {"candles": page, "nextBefore": "cursor" if page else None}

    fn.calls = calls
    return fn


def test_gap_fields_need_todays_row():
    rows = [[20260820, 90, 95, 88, 100, 1]] * 18 + [[20261020, 100, 101, 99, 100, 1], [20261021, 112, 118, 111, 115, 1]]
    assert ctx.gap_fields(rows, DAY) == (100, 112)
    assert ctx.gap_fields(rows[:-1], DAY) == (0, 0)  # today's row not published yet: no gap to report
    assert round(ctx.gap_pct(100, 112), 1) == 12.0 and ctx.gap_pct(0, 112) is None
    assert round(ctx.open_return_pct(105, 112), 1) == 6.7


def test_extended_context_reads_premarket_and_previous_evening_across_pages():
    page1 = [candle("2026-10-21T08:59", 11_150, 0), candle("2026-10-21T08:30", 11_100, 500, high=11_200),
             candle("2026-10-21T08:01", 10_900, 900, high=11_000)]
    page2 = [candle("2026-10-20T19:55", 10_500, 40), candle("2026-10-20T19:56", 10_520, 0)]
    fn = pages(page1, page2)
    result = ctx.extended_context("005930", DAY, fn)
    assert result == {"preopen_price": 11_100, "premarket_high": 11_200, "prev_evening_price": 10_500, "prev_evening_time": "2026-10-20 19:55"}
    assert fn.calls == [None, "cursor"]  # paged back until a traded candle of the previous session appeared


def test_extended_context_is_blank_when_no_candles():
    assert ctx.extended_context("005930", DAY, pages()) == {"preopen_price": "", "premarket_high": "", "prev_evening_price": "", "prev_evening_time": ""}


def make_alert(**kwargs):
    base = dict(ticker="005930", name="삼성전자", entry_price=100, close=112, return_pct=12.0, reason="1차 익절 목표 +10.0% 도달",
                holding_days=3, sale_type="partial", stage="take_profit_1", prev_close=100, open_price=112)
    return SellAlert(**{**base, **kwargs})


def test_record_exit_events_logs_gap_and_survives_a_failed_lookup(tmp_path):
    path = str(tmp_path / "events.csv")

    def broken(*_):
        raise RuntimeError("toss down")

    assert ctx.record_exit_events([make_alert()], "aggressive", DAY, path, candle_fn=broken) == 1
    assert ctx.record_exit_events([make_alert(ticker="000660")], "neutral", DAY, path, candle_fn=broken) == 1  # not primary: no lookup at all
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert [r["profile"] for r in rows] == ["aggressive", "neutral"]
    assert rows[0]["gap_pct"] == "12.00" and rows[0]["open_return_pct"] == "12.00" and rows[0]["note"].startswith("extended lookup failed")
    assert rows[1]["note"] == "" and rows[1]["preopen_price"] == ""


def test_record_exit_events_looks_up_only_take_profit_or_gap_events(tmp_path):
    path, seen = str(tmp_path / "events.csv"), []
    fn = lambda symbol, interval, count, before: seen.append(symbol) or {"candles": [candle("2026-10-21T08:30", 105)], "nextBefore": None}
    plain = make_alert(ticker="A", stage="", sale_type="full", reason="20일선 2회 연속 이탈", prev_close=100, open_price=101, return_pct=-3.0)
    gapped_stop = make_alert(ticker="B", stage="", sale_type="full", reason="손절", prev_close=100, open_price=95, return_pct=-6.0)
    ctx.record_exit_events([plain, gapped_stop, make_alert(ticker="C")], "aggressive", DAY, path, candle_fn=fn)
    assert seen == ["B", "C"]  # B: -5% gap; C: take-profit; A: neither


def test_profile_label():
    assert ctx.profile_label("logs/sell_alerts.csv") == "aggressive"
    assert ctx.profile_label("logs/sell_alerts_exp_control.csv") == "exp_control"


def test_alert_text_mentions_a_gap_only_when_it_is_large():
    gapped = format_message([make_alert()])
    assert "갭 +12.0% (전일 종가 100 → 시가 112원) · 시가 기준 수익률 +12.0%" in gapped
    assert "갭" not in format_message([make_alert(open_price=101)])  # +1%: not worth a line
    assert "갭" not in format_message([make_alert(prev_close=0, open_price=0)])  # today's row unknown


@patch("stock_alarm.toss_client.blocking_warnings_for", return_value=[])
@patch("stock_alarm.sell_check.stock_name", return_value="Samsung")
def test_check_position_carries_gap_without_changing_the_decision(_name, _warn):
    base = [[20260820 + i, 100, 101, 99, 100, 1] for i in range(18)]
    gapped = base + [[20261020, 100, 101, 99, 100, 1], [20261021, 112, 118, 111, 112, 1]]
    alert = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, DAY, price_rows=gapped, remaining_quantity=10)
    assert alert.stage == "take_profit_1" and alert.sale_type == "partial"  # same decision as before this change
    assert (alert.prev_close, alert.open_price) == (100, 112)
    unpublished = check_position({"ticker": "005930", "name": "Samsung", "entry_price": "100"}, DAY, price_rows=gapped[:-1] + [[20261020, 100, 101, 99, 112, 1]],
                                  remaining_quantity=10)
    assert unpublished.stage == "take_profit_1" and (unpublished.prev_close, unpublished.open_price) == (0, 0)
