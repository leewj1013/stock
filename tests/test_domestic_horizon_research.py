import json
from contextlib import closing
from datetime import date
from unittest.mock import patch

import pytest

from stock_alarm import domestic_horizon_research as research


def evidence(**changes):
    return {"ticker": "005930", "name": "테스트", "evaluated_at": "2026-10-23T10:00:00",
            "market_date": "2026-10-23", "close": 100, "atr20_pct": 3,
            "distance_ma20_pct": 2, "volume_ratio": 1.5, "relative_strength_pct": 2,
            "strategy_version": "test", **changes}


def bars(prices):
    return [[f"202611{i:02d}", opening, max(opening, close), min(opening, close), close, 100]
            for i, (opening, close) in enumerate(prices, 1)]


def test_harvest_is_prospective_and_day_trade_waits_for_minute_data(tmp_path):
    path = str(tmp_path / "research.db")
    with closing(research.connect(path)) as db:
        db.execute("UPDATE metadata SET value='2026-10-23T09:00:00' WHERE key='activated_at'")
        db.commit()
    with patch.object(research, "query_rows", return_value=[evidence()] ) as query:
        assert research.harvest("source.db", path) == {"swing": 1, "day": 1}
        assert research.harvest("source.db", path) == {"swing": 0, "day": 0}
        assert "c.evaluated_at>=?" in query.call_args.args[0]
    with closing(research.connect(path)) as db:
        rows = db.execute("SELECT kind,state,outcome FROM observations ORDER BY kind").fetchall()
    assert [(row["kind"], row["state"], row["outcome"]) for row in rows] == [
        ("day", "DATA_REQUIRED", None), ("swing", "PENDING", None)]


def test_swing_uses_next_open_after_close_stop_and_same_cost_baseline():
    prices = [(100, 90), (80, 82)] + [(82, 82)] * 13
    result = research.simulate_swing({"evidence": evidence()}, bars(prices))
    assert result["exit_day"] == "20261102"
    assert result["exit_price"] == 80
    assert result["swing_net_pct"] < result["hold_net_pct"]
    assert result["difference_pctp"] == pytest.approx(result["swing_net_pct"] - result["hold_net_pct"])


def test_incomplete_window_has_no_return_and_bad_bars_fail():
    payload = {"evidence": evidence()}
    assert research.simulate_swing(payload, bars([(100, 101)] * 14)) is None
    duplicate = bars([(100, 101)] * 15)
    duplicate[1][0] = duplicate[0][0]
    with pytest.raises(ValueError, match="Duplicate"):
        research.simulate_swing(payload, duplicate)


def test_run_completes_only_swing_and_version_is_frozen(tmp_path):
    path = str(tmp_path / "research.db")
    with closing(research.connect(path)) as db:
        db.execute("INSERT INTO observations(kind,ticker,signal_day,observed_at,payload,state) VALUES (?,?,?,?,?,?)",
                   ("swing", "005930", "2026-10-23", "2026-10-23T10:00:00",
                    json.dumps({"evidence": evidence()}), "PENDING"))
        db.commit()
    with patch.object(research, "harvest", return_value={"swing": 0, "day": 0}), \
         patch("stock_alarm.app.naver_rows", return_value=bars([(100, 101)] * 15)):
        result = research.run(path=path, today=date(2026, 12, 1))
    assert result["failures"] == 0
    with closing(research.connect(path)) as db:
        assert db.execute("SELECT state FROM observations").fetchone()[0] == "COMPLETE"
        db.execute("UPDATE metadata SET value='{}' WHERE key='rules'")
        db.commit()
    with pytest.raises(ValueError, match="rules changed"):
        research.connect(path)
