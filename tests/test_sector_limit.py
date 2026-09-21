import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from stock_alarm.app import Pick, auto_buy_virtual_trader, sector_limited_allocations


class SectorLimitTest(unittest.TestCase):
    def test_sector_cap_counts_locked_holdings(self):
        picks = [SimpleNamespace(ticker=value) for value in ("A", "B", "C")]
        result = sector_limited_allocations(
            picks, [25, 20, 10], sector_by_ticker={"A": "반도체", "B": "반도체", "C": "은행"},
            locked_tickers={"A"}, group_cap_override=40, minimum_override=10,
        )
        self.assertEqual(result, [25, 15, 10])

    def test_tighter_constraint_never_increases_prior_result(self):
        picks = [SimpleNamespace(ticker=value) for value in ("A", "B", "C")]
        prior = [20, 10, 10]
        result = sector_limited_allocations(picks, prior, sector_by_ticker={"A": "X", "B": "X", "C": "Y"}, group_cap_override=25, minimum_override=10)
        self.assertEqual(result, [20, 0, 10])
        self.assertTrue(all(after <= before for before, after in zip(prior, result)))

    def test_default_disabled_does_not_fetch_or_change(self):
        picks = [SimpleNamespace(ticker="A"), SimpleNamespace(ticker="B")]
        self.assertEqual(sector_limited_allocations(picks, [10, 10], group_cap_override=100), [10, 10])

    @patch.dict(os.environ, {"RISK_MAX_EXPOSURE_PCT": "70"})
    @patch("stock_alarm.data_store.virtual_buy")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.5)
    @patch("stock_alarm.app.sector_limited_allocations")
    @patch("stock_alarm.app.correlation_limited_allocations", side_effect=lambda picks, allocations: allocations)
    @patch("stock_alarm.app.allocation_percentages", return_value=[10.0])
    @patch("stock_alarm.portfolio_risk.new_buys_allowed", return_value=(True, ""))
    @patch("stock_alarm.data_store.virtual_trader_state")
    def test_auto_buy_routes_path_and_sector_cap_to_a_secondary_profile(
        self, state, _allowed, _allocations, _correlation, sector_limit, _breadth, virtual_buy,
    ):
        # A secondary virtual-trader profile (e.g. risk-neutral) must write to
        # its own DB and apply its own sector cap even when the env-driven
        # default (SECTOR_GROUP_MAX_PCT=100, i.e. off) would otherwise skip it.
        state.return_value = {"cash": 1_000_000, "holdings": [], "total_equity": 1_000_000}
        pick = Pick("005930", "Samsung", 100, 0, 0, 0)

        auto_buy_virtual_trader([pick], path="data/stock_alarm_neutral.db", sector_cap_override=30.0)

        state.assert_called_with(path="data/stock_alarm_neutral.db")
        _allowed.assert_called_with(path="data/stock_alarm_neutral.db", release_policy=None)
        sector_limit.assert_called_once()
        self.assertEqual(30.0, sector_limit.call_args.kwargs["group_cap_override"])
        virtual_buy.assert_called_once()
        self.assertEqual("data/stock_alarm_neutral.db", virtual_buy.call_args.kwargs["path"])

    @patch("stock_alarm.data_store.virtual_buy")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.app.correlation_limited_allocations", side_effect=lambda picks, allocations: allocations)
    @patch("stock_alarm.app.allocation_percentages", return_value=[10.0])
    @patch("stock_alarm.portfolio_risk.new_buys_allowed", return_value=(True, ""))
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"cash": 1_000_000, "holdings": [], "total_equity": 1_000_000})
    def test_profile_exposure_cap_is_tighter_than_aggressive_market_cap(
        self, _state, _allowed, _allocations, _correlation, _breadth, virtual_buy,
    ):
        pick = Pick("005930", "Samsung", 100, 0, 0, 0)

        auto_buy_virtual_trader([pick], exposure_limit_override=50.0)

        candidates = virtual_buy.call_args.args[0]
        self.assertEqual(50.0, candidates[0]["portfolio_limit_pct"])

    @patch.dict(os.environ, {"RISK_MAX_EXPOSURE_PCT": "70"})
    @patch("stock_alarm.data_store.virtual_buy")
    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.app.correlation_limited_allocations", side_effect=lambda picks, allocations: allocations)
    @patch("stock_alarm.app.allocation_percentages", return_value=[10.0])
    @patch("stock_alarm.portfolio_risk.new_buys_allowed", return_value=(True, ""))
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"cash": 1_000_000, "holdings": [], "total_equity": 1_000_000})
    def test_default_profile_never_buys_past_the_risk_exposure_limit(
        self, _state, _allowed, _allocations, _correlation, _breadth, _regime, virtual_buy,
    ):
        pick = Pick("005930", "Samsung", 100, 0, 0, 0)

        auto_buy_virtual_trader([pick])

        candidates = virtual_buy.call_args.args[0]
        self.assertEqual(70.0, candidates[0]["portfolio_limit_pct"])

    @patch.dict(os.environ, {"RISK_MAX_EXPOSURE_PCT": "70"})
    @patch("stock_alarm.data_store.virtual_buy")
    @patch("stock_alarm.app.current_market_regime", return_value="sideways")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.app.correlation_limited_allocations", side_effect=lambda picks, allocations: allocations)
    @patch("stock_alarm.app.allocation_percentages", return_value=[10.0])
    @patch("stock_alarm.portfolio_risk.new_buys_allowed", return_value=(True, ""))
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"cash": 1_000_000, "holdings": [], "total_equity": 1_000_000})
    def test_regime_exposure_multiplier_halves_the_cap_in_a_sideways_market(
        self, _state, _allowed, _allocations, _correlation, _breadth, _regime, virtual_buy,
    ):
        pick = Pick("005930", "Samsung", 100, 0, 0, 0)

        auto_buy_virtual_trader([pick], regime_exposure_multiplier={"sideways": 0.5})

        candidates = virtual_buy.call_args.args[0]
        self.assertEqual(35.0, candidates[0]["portfolio_limit_pct"])

    @patch.dict(os.environ, {"RISK_MAX_EXPOSURE_PCT": "70"})
    @patch("stock_alarm.data_store.virtual_buy")
    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.app.correlation_limited_allocations", side_effect=lambda picks, allocations: allocations)
    @patch("stock_alarm.app.allocation_percentages", return_value=[10.0])
    @patch("stock_alarm.portfolio_risk.new_buys_allowed", return_value=(True, ""))
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"cash": 1_000_000, "holdings": [], "total_equity": 1_000_000})
    def test_regime_exposure_multiplier_is_unaffected_outside_its_configured_regime(
        self, _state, _allowed, _allocations, _correlation, _breadth, _regime, virtual_buy,
    ):
        pick = Pick("005930", "Samsung", 100, 0, 0, 0)

        auto_buy_virtual_trader([pick], regime_exposure_multiplier={"sideways": 0.5})

        candidates = virtual_buy.call_args.args[0]
        self.assertEqual(70.0, candidates[0]["portfolio_limit_pct"])

    @patch.dict("os.environ", {"RISK_MAX_EXPOSURE_PCT": "70"})
    @patch("stock_alarm.data_store.virtual_buy")
    @patch("stock_alarm.app.current_market_regime", return_value="bull")
    @patch("stock_alarm.app.naver_market_up_ratio", return_value=0.7)
    @patch("stock_alarm.app.correlation_limited_allocations", side_effect=lambda picks, allocations: allocations)
    @patch("stock_alarm.app.allocation_percentages", return_value=[10.0])
    @patch("stock_alarm.portfolio_risk.buy_allocation_scale", return_value=(0.3, "drawdown_cooldown_reentry"))
    @patch("stock_alarm.portfolio_risk.new_buys_allowed", return_value=(True, "drawdown_cooldown_reentry"))
    @patch("stock_alarm.data_store.virtual_trader_state", return_value={"cash": 1_000_000, "holdings": [], "total_equity": 1_000_000})
    def test_cooldown_reentry_scales_allocations_and_forwards_the_release_policy(
        self, _state, _allowed, _scale, _allocations, _correlation, _breadth, _regime, virtual_buy,
    ):
        policy = {"mode": "cooldown", "cooldown_days": 20, "reentry_scale": 0.3}

        auto_buy_virtual_trader([Pick("005930", "Samsung", 100, 0, 0, 0)], risk_release_policy=policy)

        self.assertEqual(3.0, virtual_buy.call_args.args[0][0]["allocation_pct"])
        self.assertEqual(policy, virtual_buy.call_args.kwargs["risk_release_policy"])


if __name__ == "__main__":
    unittest.main()
