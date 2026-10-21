import json
from contextlib import closing
from datetime import date
from unittest.mock import patch

import pytest

from stock_alarm import risk_research as research


def attempt():
    return {"observed_at": "2026-10-21T09:09:38", "attempt_id": "a1",
            "state": {"cash": 100_000_000, "total_equity": 100_000_000, "holdings": []},
            "stop_rules": {"floor": 5.0, "atr_multiplier": 2.0},
            "evidence": {"243840": {"evaluated_at": "2026-10-21T09:05:00", "atr20_pct": 7.8}}}


def test_actual_fill_sizing_and_wide_stop_skip():
    plan = research.plan(attempt(), {"ticker": "243840", "name": "신흥에스이씨", "price": 6570, "quantity": 2405})
    assert plan["entry_stop_pct"] == pytest.approx(15.6)
    assert plan["risk_sized_quantity"] == 487
    assert plan["skip_wide_stop"] is True
    assert research.plan({**attempt(), "observed_at": "2026-10-21T09:00:00"},
                         {"ticker": "243840", "price": 6570, "quantity": 2405}) is None


def test_fixed_stop_does_not_widen_and_next_open_gap_is_counted():
    plan = research.plan(attempt(), {"ticker": "243840", "price": 6570, "quantity": 2405})
    bars = [["20261022", 6570, 6600, 5300, 5500],
            ["20261023", 5000, 5500, 4900, 5500],
            ["20261026", 5500, 5600, 5300, 5500]]
    result = research.simulate(plan, bars, {"20261022": 20.0, "20261023": 20.0, "20261026": 20.0})
    assert result["1"]["dynamic_stop_pct"] == 20
    assert result["3"]["exit_price"]["fixed_entry_stop"] == 5000
    assert result["3"]["stop_overshoot_pct"]["fixed_entry_stop"] > 0
    assert result["3"]["post_exit_return_pct"]["fixed_entry_stop"] > 0
    assert result["3"]["exit_price"]["current"] is None
    assert result["3"]["difference_pct"]["risk_sized"] > 0
    assert result["3"]["difference_pct"]["skip_wide"] > 0


def test_missing_dynamic_check_is_not_imputed():
    plan = research.plan(attempt(), {"ticker": "243840", "price": 6570, "quantity": 2405})
    result = research.simulate(plan, [["20261022", 6570, 6700, 6200, 6500]], {})["1"]
    assert result["contribution_pct"]["current"] is None
    assert result["difference_pct"]["fixed_entry_stop"] is None
    assert result["contribution_pct"]["skip_wide"] == 0


def test_harvest_only_after_activation_and_one_case_per_fill(tmp_path):
    path = str(tmp_path / "risk.db")
    with closing(research.connect(path)) as db:
        db.execute("UPDATE metadata SET value='2026-10-21T09:00:00' WHERE key='activated_at'")
        db.commit()
    row = {"attempt_id": "a1", "observed_at": attempt()["observed_at"],
           "payload": json.dumps({**attempt(), "executions": [{"ticker": "243840", "price": 6570, "quantity": 2405}]}),
           "error": None}
    with patch.object(research, "query_rows", return_value=[row]) as query:
        assert research.harvest(path=path) == 1
        assert research.harvest(path=path) == 0
        assert "observed_at>=?" in query.call_args.args[0]
    with closing(research.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1


def test_dashboard_empty_and_version_guard(tmp_path):
    path = str(tmp_path / "risk.db")
    assert "체결을 기다리고" in research.dashboard_section(path)
    with closing(research.connect(path)) as db:
        db.execute("UPDATE metadata SET value='{}' WHERE key='rules'")
        db.commit()
    with pytest.raises(ValueError, match="rules changed"):
        research.connect(path)
