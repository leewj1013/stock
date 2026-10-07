from types import SimpleNamespace

from stock_alarm import us_strategy_account as acc


def row(date, event="buy", ticker="AAPL.O", shares=10, price="100.0000", reason="entry"):
    return {"date": date, "event": event, "ticker": ticker, "shares": shares, "price_usd": price, "reason": reason}


def test_decision_rows_convert_cents_to_dollars():
    result = SimpleNamespace(events=[{"date": "2026-10-07", "event": "buy", "ticker": "AAPL.O", "shares": 10, "price": 32513.0, "reason": "entry"}])
    assert acc.decision_rows(result)[0]["price_usd"] == "325.1300"


def test_diverges_accepts_prefix_and_new_orders():
    logged = [row("2026-10-07")]
    assert acc.diverges([], [row("2026-10-07")]) == []
    assert acc.diverges(logged, [row("2026-10-07"), row("2026-10-08", "sell_full", reason="stop")]) == []


def test_diverges_flags_changed_missing_or_extra_orders_on_logged_days():
    logged = [row("2026-10-07")]
    assert acc.diverges(logged, [row("2026-10-07", price="99.0000")])  # price changed
    assert acc.diverges(logged, [])  # order vanished
    assert acc.diverges(logged, [row("2026-10-07"), row("2026-10-07", ticker="MSFT.O")])  # unlogged order on a logged day
