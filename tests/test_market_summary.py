import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.market_summary import append_us_history, krx_top_trading_value_leaders, market_regime, market_rows, message, naver_world_index, run, summary, us_gap_note, whole_market_summary


class MarketSummaryTest(unittest.TestCase):
    def test_summary(self):
        rows = [{"change_pct": "1.00"}, {"change_pct": "-2.00"}, {"change_pct": "0.00"}]
        self.assertEqual({"count": "3", "up_count": "1", "down_count": "1", "up_ratio_pct": "33.3", "avg_change_pct": "-0.33"}, summary(rows))

    def test_message(self):
        text = message(
            [{"ticker": "A", "name": "Alpha", "change_pct": "1.23", "trading_value": "100"}, {"ticker": "B", "name": "Beta", "change_pct": "-2.34", "trading_value": "200"}],
            [{"symbol": ".INX", "name": "S&P 500", "close": "7650.50", "change_pct": "1.10"}, {"symbol": ".VIX", "name": "VIX", "close": "14.81", "change_pct": "-4.08"}],
            whole_market={},
            whole_market_leaders=[],
        )
        self.assertIn("[08:30 오늘의 매매 브리핑]", text)
        self.assertIn("미국 증시 마감", text)
        self.assertIn("- S&P 500 +1.10%", text)
        self.assertIn("- VIX 14.81 (-4.08%)", text)
        self.assertIn("시초가에서 평균 1% 안팎 상승 출발", text)
        self.assertIn("상승/하락: 1개 / 1개", text)
        self.assertIn("거래대금 주도 종목(관심종목)", text)
        self.assertIn("권장 신규 매수 한도", text)

    def test_message_includes_whole_market_section_and_leaders_when_available(self):
        text = message(
            [{"ticker": "A", "name": "Alpha", "change_pct": "1.23", "trading_value": "100"}],
            [],
            whole_market={"up_ratio_pct": "62.0", "avg_change_pct": "0.45"},
            whole_market_leaders=[{"ticker": "005930", "name": "삼성전자", "change_pct": -1.2}],
        )
        self.assertIn("국내 전체 시장(코스피·코스닥)", text)
        self.assertIn("상승 비율: 62.0%", text)
        self.assertIn("거래대금 주도 종목(전체 시장)", text)
        self.assertIn("삼성전자(005930): -1.20%", text)

    @patch("stock_alarm.market_summary.whole_market_summary", return_value=None)
    @patch("stock_alarm.market_summary.us_market_rows", return_value=[])
    @patch("stock_alarm.market_summary.krx_top_trading_value_leaders")
    @patch("stock_alarm.market_summary.market_rows")
    @patch("stock_alarm.market_summary.session_change")
    @patch("stock_alarm.market_summary.latest_naver_trading_day", return_value=date(2026, 9, 18))
    def test_every_percentage_comes_from_the_same_naver_session(self, _day, session_change_mock, market_rows_mock, krx, _us, _whole):
        # 2026-09-18: KRX's rows disagreed with Naver and Toss, so SK hynix
        # showed +4.13% and -0.80% in adjacent lists.
        market_rows_mock.return_value = [{"ticker": "000660", "name": "SK하이닉스", "change_pct": "4.70", "trading_value": "9"}]
        krx.return_value = [{"ticker": "000660", "name": "SK하이닉스", "change_pct": -0.8}]
        session_change_mock.return_value = (4.7, 9)

        text = message()

        market_rows_mock.assert_called_once_with(date(2026, 9, 18))
        session_change_mock.assert_called_once_with("000660", date(2026, 9, 18))
        self.assertIn("SK하이닉스(000660): +4.70%", text.split("전체 시장")[1])
        self.assertNotIn("-0.80%", text)
        self.assertIn("■ 국내 관심종목 흐름 (09/18 기준)", text)
        self.assertIn("국내는 09/18 종가 기준입니다.", text)

    def test_identical_leader_lists_are_shown_once(self):
        rows = [{"ticker": "005930", "name": "삼성전자", "change_pct": "1.46", "trading_value": "9"}]
        same = [{"ticker": "005930", "name": "삼성전자", "change_pct": 1.46}]
        text = message(rows, [], whole_market={}, whole_market_leaders=same)
        self.assertNotIn("거래대금 주도 종목(관심종목)", text)
        self.assertIn("거래대금 주도 종목(전체 시장)", text)

    @patch("stock_alarm.market_summary.session_change", return_value=None)
    def test_leader_keeps_krx_figure_when_naver_has_nothing(self, _change):
        from stock_alarm.market_summary import with_session_changes

        leaders = [{"ticker": "A", "change_pct": -1.0}]
        self.assertEqual(leaders, with_session_changes(leaders, date(2026, 9, 18)))

    def test_guidance_is_not_repeated_as_a_closing_conclusion(self):
        text = message([{"ticker": "A", "name": "Alpha", "change_pct": "1.0", "trading_value": "1"}], [], whole_market={}, whole_market_leaders=[])
        self.assertNotIn("한 줄 결론", text)
        self.assertEqual(1, text.count("초반 추격을 피하고") + text.count("추세 확인 종목은") + text.count("신규 매수를 최소화"))

    def test_market_regime_ignores_us_market(self):
        result = market_regime({"avg_change_pct": "2.0", "up_ratio_pct": "80.0"})
        self.assertEqual("🟢 공격", result["label"])
        self.assertEqual("70%", result["buy_limit"])

    def test_us_gap_note_only_for_large_sp500_moves(self):
        self.assertEqual("", us_gap_note([{"symbol": ".INX", "change_pct": "0.99"}]))
        self.assertIn("하락 출발", us_gap_note([{"symbol": ".INX", "change_pct": "-1.20"}]))
        self.assertEqual("", us_gap_note([{"symbol": ".IXIC", "change_pct": "3.00"}]))

    def test_market_regime_prefers_whole_market_over_watchlist_when_given(self):
        result = market_regime(
            {"avg_change_pct": "5.0", "up_ratio_pct": "90.0"},
            whole_market={"avg_change_pct": "-2.0", "up_ratio_pct": "20.0"},
        )
        self.assertEqual("🔴 방어", result["label"])

    @patch("stock_alarm.market_breadth.cached_whole_market_average_change_pct", return_value=1.2)
    @patch("stock_alarm.market_breadth.cached_whole_market_up_ratio", return_value=0.6)
    def test_whole_market_summary_formats_ratio_as_percent(self, _ratio, _avg):
        self.assertEqual({"up_ratio_pct": "60.0", "avg_change_pct": "1.20"}, whole_market_summary())

    @patch("stock_alarm.market_breadth.cached_whole_market_average_change_pct", return_value=None)
    @patch("stock_alarm.market_breadth.cached_whole_market_up_ratio", return_value=None)
    def test_whole_market_summary_returns_none_when_unavailable(self, _ratio, _avg):
        self.assertIsNone(whole_market_summary())

    @patch("stock_alarm.market_breadth.krx_top_trading_value_rows", side_effect=RuntimeError("network"))
    def test_krx_top_trading_value_leaders_swallows_errors(self, _rows):
        self.assertEqual([], krx_top_trading_value_leaders())

    @patch("stock_alarm.market_summary.urllib.request.urlopen")
    def test_naver_world_index_parses_close_and_signed_change(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = '{"closePrice":"7,650.50","fluctuationsRatio":"-1.25","localTradedAt":"2026-09-18T17:29:48-04:00"}'.encode()
        self.assertEqual({"symbol": ".INX", "name": "S&P 500", "market_date": "2026-09-18", "close": "7650.50", "change_pct": "-1.25"}, naver_world_index(".INX"))

    def test_append_us_history_skips_rows_already_recorded(self):
        import csv, os, tempfile

        row = {"symbol": ".VIX", "name": "VIX", "market_date": "2026-09-18", "close": "14.81", "change_pct": "-4.08"}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "history.csv")
            append_us_history([row], path)
            append_us_history([row, {**row, "market_date": "2026-09-21"}], path)
            with open(path, encoding="utf-8", newline="") as file:
                self.assertEqual(["2026-09-18", "2026-09-21"], [r["market_date"] for r in csv.DictReader(file)])

    @patch("stock_alarm.market_summary.stock_name", side_effect=lambda _ticker, fallback: fallback)
    @patch("stock_alarm.market_summary.configured_stocks", return_value={"005930": "삼성전자"})
    @patch("stock_alarm.market_summary.naver_rows", return_value=[[20260728, 0, 0, 0, 100, 10], [20260729, 0, 0, 0, 110, 20]])
    def test_market_rows(self, _rows, _stocks, _name):
        self.assertEqual([{"ticker": "005930", "name": "삼성전자", "change_pct": "10.00", "trading_value": "2200"}], market_rows(date(2026, 7, 29)))

    @patch("stock_alarm.market_summary.load_env")
    @patch("stock_alarm.market_summary.message", return_value="summary")
    @patch("stock_alarm.market_summary.send_notification", return_value="telegram")
    def test_run_sends_previous_session_summary_before_open(self, send, _message, _env):
        self.assertEqual("telegram", run())
        send.assert_called_once_with("summary")


if __name__ == "__main__":
    unittest.main()
