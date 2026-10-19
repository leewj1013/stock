import json
import sqlite3
from contextlib import closing
from datetime import date
from unittest.mock import patch

import pytest

from stock_alarm import entry_research as research


def evidence(**changes):
    return {"ticker": "005930", "name": "테스트", "evaluated_at": "2026-10-19T09:10:00",
            "market_date": "2026-10-19", "close": 100, "atr20_pct": 3,
            "distance_ma20_pct": 7, "volume_ratio": 2.5, "relative_strength_pct": 2, **changes}


def bars(prices):
    return [[f"202610{20+i:02d}", o, max(o, c), min(o, c), c, 100] for i, (o, c) in enumerate(prices)]


def test_recommendation_evidence_freezes_risk_and_variants():
    result = research.plan(evidence())
    assert result["thesis"] == "trend_volume"
    assert result["weights"]["avoid_stretched"] == 0
    assert result["weights"]["risk_sized"] == pytest.approx(.005 / .06)
    assert result["risk_limit_pct"] == 6
    assert result["evidence"]["relative_strength_pct"] == 2
    assert result["planned_sessions"] == 10


@pytest.mark.parametrize("changes", [{"atr20_pct": None}, {"atr20_pct": float('nan')},
                                     {"distance_ma20_pct": None}, {"market_date": "2026-10-16"}])
def test_missing_or_stale_evidence_cannot_become_a_trade(changes):
    p = research.plan(evidence(**changes))
    assert p["state"] == "DATA_REQUIRED"
    assert research.simulate(p, bars([(100, 110)])) == {}


def test_skipping_a_winner_has_opportunity_cost_and_immature_horizons_absent():
    result = research.simulate(research.plan(evidence()), bars([(100, 110)]))["horizons"]
    assert result["1"]["difference_pct"]["avoid_stretched"] < 0
    assert result["1"]["difference_pct"]["risk_sized"] < 0
    assert "3" not in result and "10" not in result


def test_stop_uses_next_open_and_retains_exit_recovery_diagnostic():
    p = research.plan(evidence())
    r = research.simulate(p, bars([(100, 90), (80, 85), (85, 110)]))
    third = r["horizons"]["3"]
    assert third["exit_price"] == 80
    assert third["post_exit_return_pct"] == pytest.approx(37.5)
    assert third["stock_net_return_pct"] == pytest.approx((.8 * .9985 / 1.0015 - 1) * 100)
    assert third["difference_pct"]["risk_sized"] > 0
    assert third["first_session_return_pct"] == pytest.approx(-10)


def test_harvest_is_prospective_and_keeps_first_signal_per_day(tmp_path):
    path = str(tmp_path / "research.db")
    with closing(research.connect(path)) as db:
        db.execute("UPDATE metadata SET value='2026-10-19T09:00:00' WHERE key='activated_at'")
        db.commit()
    # Query predicate is separately asserted so earlier recommendations cannot enter.
    with patch.object(research, "query_rows", return_value=[evidence(), evidence(evaluated_at="2026-10-19T10:00:00", atr20_pct=9)]) as query:
        assert research.harvest("source.db", path) == 1
        assert research.harvest("source.db", path) == 0
        assert "c.evaluated_at>=?" in query.call_args.args[0]
        assert query.call_args.args[1] == ("2026-10-19T09:00:00",)
    with closing(research.connect(path)) as db:
        row = db.execute("SELECT * FROM entries").fetchone()
        assert json.loads(row["payload"])["risk_limit_pct"] == 6


def test_completion_requires_ten_finished_sessions_and_fetch_failure_retries(tmp_path):
    path = str(tmp_path / "research.db")
    p = research.plan(evidence())
    with closing(research.connect(path)) as db:
        db.execute("INSERT INTO entries(ticker,signal_day,observed_at,payload,state) VALUES (?,?,?,?,?)",
                   ('005930', '2026-10-19', '2026-10-19T09:10:00', json.dumps(p), 'PENDING'))
        db.commit()
    with patch.object(research, 'harvest', return_value=0), patch('stock_alarm.app.naver_rows', side_effect=OSError('feed unavailable')):
        assert research.run(path=path, today=date(2026, 10, 30))["failures"] == 1
    with patch.object(research, 'harvest', return_value=0), patch('stock_alarm.app.naver_rows', return_value=bars([(100, 101)] * 10)):
        assert research.run(path=path, today=date(2026, 10, 30))["failures"] == 0
    with closing(research.connect(path)) as db:
        row = db.execute('SELECT state,error FROM entries').fetchone()
        assert row['state'] == 'COMPLETE' and row['error'] is None


def test_readonly_empty_dashboard_and_rule_version_guard(tmp_path):
    path = str(tmp_path / "research.db")
    assert "새 추천을 기다리고" in research.dashboard_section(path)
    with closing(research.connect(path)) as db:
        db.execute("UPDATE metadata SET value='{}' WHERE key='rules'")
        db.commit()
    with pytest.raises(ValueError, match="rules changed"):
        research.connect(path)
