import unittest
from datetime import datetime as real_datetime
from unittest.mock import patch

from stock_alarm.daily_summary import latest_recommendations, market_comparison_line, message, run
# bound at import, before setUp swaps the module attributes for stubs
from stock_alarm.daily_summary import screener_lines as real_screener_lines, shadow_lines as real_shadow_lines


class DailySummaryTest(unittest.TestCase):
    def setUp(self):
        # message() also reads today's shadow orders and the saved screener
        # result; keep those off the real DB and reports folder.
        for name in ("shadow_lines", "screener_lines"):
            patcher = patch(f"stock_alarm.daily_summary.{name}", return_value=[])
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch("stock_alarm.market_summary.whole_market_summary", return_value=None)
    @patch("stock_alarm.daily_summary.virtual_deposits_since", return_value=0)
    @patch("stock_alarm.daily_summary.previous_virtual_valuation", return_value={"equity": 9900000})
    @patch("stock_alarm.daily_summary.virtual_trader_state")
    @patch("stock_alarm.daily_summary.current_prices", return_value={"A": 11000})
    @patch("stock_alarm.daily_summary.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.daily_summary.recent_virtual_trades")
    @patch("stock_alarm.daily_summary.datetime")
    @patch("stock_alarm.daily_summary.tail_csv")
    def test_message(self, tail_csv, datetime, trades, _sales, _prices, state, _previous, _deposits, _whole_market):
        datetime.now.return_value = real_datetime(2026, 7, 31, 16, 0)
        trades.return_value = [{"created_at": "2026-07-31T09:10:00", "ticker": "A", "name": "Alpha", "quantity": 2, "allocation_pct": 20}]
        state.return_value = {
            "total_equity": 10_000_000, "total_return_pct": 1.25, "cash": 8_000_000,
            "holdings_value": 2_000_000, "holdings_return_pct": 2.5,
            "holdings": [{"ticker": "A", "name": "Alpha", "return_pct": 2.5}],
        }
        def fake_tail(path, _count):
            if path.endswith("recommendations.csv"):
                return [{"created_at": "2026-07-31T09:00:00", "ticker": "A", "name": "Alpha"}, {"created_at": "2026-07-31T09:00:00", "ticker": "B", "name": "Beta"}]
            return []

        tail_csv.side_effect = fake_tail
        text = message()
        self.assertIn("[주식 마감 브리핑 | 07/31]", text)
        self.assertIn("추천 2종목 · 가상매수 1종목 · 가상매도 0종목", text)
        self.assertIn("오늘 손익 100,000원 (+1.01%)", text)
        self.assertIn("현금 8,000,000원 · 주식 2,000,000원", text)
        self.assertIn("최고 Alpha +2.50%", text)
        self.assertIn("매수: Alpha 2주 · 비중 20%", text)

    def test_unbought_picks_are_listed_since_they_are_no_longer_sent_one_by_one(self):
        from stock_alarm.daily_summary import unbought_recommendation_lines

        recommendations = [{"ticker": t, "name": n} for t, n in (("A", "Alpha"), ("B", "Beta"), ("C", "Gamma"))]
        lines = unbought_recommendation_lines(recommendations, [{"ticker": "A"}])
        self.assertEqual(["", "■ 오늘 추천(미매수)", "Beta, Gamma"], lines)
        many = [{"ticker": str(i), "name": f"N{i}"} for i in range(7)]
        self.assertEqual("N0, N1, N2, N3, N4 외 2종목", unbought_recommendation_lines(many, [])[-1])
        self.assertEqual([], unbought_recommendation_lines(recommendations[:1], [{"ticker": "A"}]))

    @patch("stock_alarm.data_store.query_rows")
    def test_shadow_lines_summarise_what_the_real_account_would_have_done(self, rows):
        rows.return_value = [{"side": "BUY", "cost": 20_000_000}, {"side": "BUY", "cost": 15_000_000}, {"side": "SELL", "cost": 0}]
        self.assertEqual(["", "■ 실계좌였다면(섀도)", "매수 2건 · 35,000,000원 · 매도 1건"], real_shadow_lines())
        rows.return_value = []
        self.assertEqual("주문 없음", real_shadow_lines()[-1])

    @patch("stock_alarm.screener.new_matches")
    def test_screener_lines_list_only_new_entrants(self, new_matches):
        new_matches.return_value = ([{"ticker": "A", "name": "Alpha"}], 37)
        self.assertEqual(["", "■ 재무 스크리닝", "신규 통과 1종목: Alpha", "(전체 37종목)"], real_screener_lines())
        new_matches.return_value = ([], 37)
        self.assertEqual("신규 통과 없음 (전체 37종목)", real_screener_lines()[-1])
        new_matches.return_value = ([], 0)
        self.assertEqual([], real_screener_lines())

    def test_market_comparison_line_shows_gap_versus_whole_market_average(self):
        line = market_comparison_line(1.5, {"up_ratio_pct": "40.0", "avg_change_pct": "-0.5"})

        self.assertEqual("계좌 대비 시장: +2.00%p (시장 평균 -0.50%)", line)

    def test_market_comparison_line_none_when_daily_return_unavailable(self):
        self.assertIsNone(market_comparison_line(None, {"up_ratio_pct": "40.0", "avg_change_pct": "-0.5"}))

    def test_market_comparison_line_none_when_whole_market_unavailable(self):
        self.assertIsNone(market_comparison_line(1.5, None))

    @patch("stock_alarm.market_summary.whole_market_summary", return_value={"up_ratio_pct": "62.0", "avg_change_pct": "0.30"})
    @patch("stock_alarm.daily_summary.virtual_deposits_since", return_value=0)
    @patch("stock_alarm.daily_summary.previous_virtual_valuation", return_value={"equity": 9900000})
    @patch("stock_alarm.daily_summary.virtual_trader_state")
    @patch("stock_alarm.daily_summary.current_prices", return_value={"A": 11000})
    @patch("stock_alarm.daily_summary.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.daily_summary.recent_virtual_trades", return_value=[])
    @patch("stock_alarm.daily_summary.datetime")
    @patch("stock_alarm.daily_summary.tail_csv", return_value=[])
    def test_message_includes_whole_market_section_when_available(self, _tail, datetime, _trades, _sales, _prices, state, _previous, _deposits, _whole_market):
        datetime.now.return_value = real_datetime(2026, 7, 31, 16, 0)
        state.return_value = {
            "total_equity": 10_000_000, "total_return_pct": 1.25, "cash": 8_000_000,
            "holdings_value": 2_000_000, "holdings_return_pct": 2.5, "holdings": [],
        }

        text = message()

        self.assertIn("■ 오늘 시장(코스피·코스닥)", text)
        self.assertIn("상승 비율: 62.0%", text)
        self.assertIn("계좌 대비 시장:", text)

    @patch("stock_alarm.daily_summary.tail_csv")
    @patch("stock_alarm.daily_summary.datetime")
    def test_latest_recommendations_uses_all_today_batches_and_dedupes(self, datetime, tail_csv):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-31"
        def fake_tail(path, _count):
            if path.endswith("recommendations.csv"):
                return [{"created_at": "2026-07-30T15:00:00", "ticker": "N"}, {"created_at": "2026-07-31T09:00:00", "ticker": "N"}, {"created_at": "2026-07-31T10:00:00", "ticker": "K"}, {"created_at": "2026-07-31T11:00:00", "ticker": "N"}]
            return []

        tail_csv.side_effect = fake_tail
        self.assertEqual(["K", "N"], [row["ticker"] for row in latest_recommendations()])

    @patch("stock_alarm.daily_summary.load_env")
    @patch("stock_alarm.daily_summary.is_trading_day", return_value=False)
    @patch("stock_alarm.daily_summary.send_notification")
    def test_run_skips_when_market_closed(self, send, _trading, _env):
        self.assertEqual("market_closed", run())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
