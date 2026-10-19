import json
import sqlite3
from contextlib import closing

import pytest

from stock_alarm import sell_research as research


def snapshot(**changes):
    return {"run_id": "r1", "position_id": "p1", "ticker": "005930", "checked_at": "2026-10-19T15:30:00",
            "market_date": "2026-10-19", "entry_price": 100, "close": 100, "return_pct": 0,
            "atr20_pct": 2, "distance_ma20_pct": -0.5, "decision": "SELL", "sale_type": "full",
            "ma20_break_triggered": 1, **changes}


def bars(prices):
    return [[f"202610{20+i:02d}", opening, max(opening, close), min(opening, close), close, 1000]
            for i, (opening, close) in enumerate(prices)]


def test_hard_risk_and_warnings_cannot_be_overridden_by_trend():
    assert research.review(snapshot(return_pct=-6), 5)["candidate_decision"] == "SELL"
    assert research.review(snapshot(reasons="종목 경고 발생: 위험"), 5)["candidate_decision"] == "SELL"
    assert research.review(snapshot(close=None), 5)["candidate_decision"] == "DATA_REQUIRED"
    assert research.review(snapshot(), 5)["candidate_decision"] == "DEFER_ONE_SESSION"
    assert research.review(snapshot(distance_ma20_pct=-3), 5)["candidate_decision"] == "SELL"


def test_defer_expires_after_one_session_and_uses_next_open_with_gap():
    payload = research.review(snapshot(), 5)
    result = research.simulate(payload, bars([(100, 101), (80, 85), (90, 95)]))
    assert result["1"]["remaining_fraction"] == 1
    assert result["3"]["remaining_fraction"] == 0
    assert result["3"]["candidate_return_pct"] == pytest.approx(-20.24)
    assert result["3"]["baseline_return_pct"] == pytest.approx(-0.3)
    assert "5" not in result  # unfinished horizons are absent, never zero


def test_partial_profit_trailing_stop_sells_residual_at_following_open():
    payload = research.review(snapshot(ma20_break_triggered=0, take_profit_triggered=1, return_pct=20), 5)
    result = research.simulate(payload, bars([(120, 125), (125, 115), (100, 102)]))
    assert result["1"]["remaining_fraction"] == 0.5
    assert result["3"]["remaining_fraction"] == 0
    assert result["3"]["candidate_return_pct"] == pytest.approx(((0.5 + 0.5 * 100 / 120) * .997 - 1) * 100)


def test_frozen_risk_can_exit_when_baseline_holds():
    payload = research.review(snapshot(decision="HOLD", return_pct=-8), 5)
    result = research.simulate(payload, bars([(90, 80)]))["1"]
    assert result["baseline_return_pct"] == pytest.approx((80 / 90 - 1) * 100)
    assert result["candidate_return_pct"] == pytest.approx(-0.3)


def test_harvest_freezes_first_risk_and_deduplicates_episodes(tmp_path):
    source, target = str(tmp_path / "source.db"), str(tmp_path / "research.db")
    first = snapshot(decision="HOLD", ma20_break_triggered=0)
    with closing(research.connect(target)) as db:
        db.execute("UPDATE metadata SET value='2026-10-19T00:00:00' WHERE key='activated_at'")
        db.commit()
    with closing(sqlite3.connect(source)) as db:
        db.execute("CREATE TABLE strategy_runs(run_id TEXT,market_date TEXT)")
        columns = [k for k in first if k != "market_date"]
        db.execute("CREATE TABLE position_checks(" + ",".join(f"{k} " + ("REAL" if isinstance(first[k], (int, float)) else "TEXT") for k in columns) + ")")
        for row in (first, snapshot(run_id="r2", checked_at="2026-10-20T15:30:00", market_date="2026-10-20", atr20_pct=9),
                    snapshot(run_id="r3", checked_at="2026-10-20T15:35:00", market_date="2026-10-20", atr20_pct=9)):
            db.execute("INSERT INTO strategy_runs VALUES (?,?)", (row["run_id"], row["market_date"]))
            db.execute("INSERT INTO position_checks VALUES (" + ",".join("?" for _ in columns) + ")", [row[k] for k in columns])
        db.commit()
    assert research.harvest(source, target) == 3
    assert research.harvest(source, target) == 0
    with closing(research.connect(target)) as db:
        assert db.execute("SELECT count(*) FROM episodes").fetchone()[0] == 1
        assert db.execute("SELECT limit_pct FROM risks").fetchone()[0] == 5
        assert json.loads(db.execute("SELECT payload FROM episodes").fetchone()[0])["risk_limit_pct"] == 5


def test_rule_changes_require_new_versioned_database(tmp_path):
    path = str(tmp_path / "research.db")
    with closing(research.connect(path)) as db:
        db.execute("UPDATE metadata SET value='{}' WHERE key='rules'")
        db.commit()
    with pytest.raises(ValueError, match="rules changed"):
        research.connect(path)


def test_invalid_prices_do_not_produce_performance():
    with pytest.raises(ValueError, match="OHLC"):
        research.simulate(research.review(snapshot(), 5), bars([(0, 100)]))


def test_stale_snapshot_requires_data_and_empty_dashboard_has_no_fake_returns(tmp_path):
    assert research.review(snapshot(market_date="2026-10-16"), 5)["candidate_decision"] == "DATA_REQUIRED"
    page = research.dashboard_section(str(tmp_path / "missing.db"))
    assert "아직 없습니다" in page
    assert "+0.00%" not in page
