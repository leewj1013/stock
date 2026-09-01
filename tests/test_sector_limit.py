import unittest
from types import SimpleNamespace
from unittest.mock import patch

from stock_alarm.app import Pick, auto_buy_virtual_trader, sector_limited_allocations
from stock_alarm.sector_reference import parse_sector_detail, parse_sector_list


class SectorLimitTest(unittest.TestCase):
    def test_parsers_extract_sector_and_ticker(self):
        sectors = parse_sector_list('<a href="/sise/sise_group_detail.naver?type=upjong&amp;no=261">반도체와반도체장비</a>')
        stocks = parse_sector_detail('<tr><td><a href="/item/main.naver?code=005930" class="tltle">삼성전자</a></td></tr>')
        self.assertEqual(sectors, [{"number": "261", "sector": "반도체와반도체장비"}])
        self.assertEqual(stocks, [{"ticker": "005930", "name": "삼성전자"}])

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
        _allowed.assert_called_with(path="data/stock_alarm_neutral.db")
        sector_limit.assert_called_once()
        self.assertEqual(30.0, sector_limit.call_args.kwargs["group_cap_override"])
        virtual_buy.assert_called_once()
        self.assertEqual("data/stock_alarm_neutral.db", virtual_buy.call_args.kwargs["path"])


if __name__ == "__main__":
    unittest.main()
