import os
import tempfile
import unittest
from unittest.mock import patch

from datetime import datetime

from stock_alarm.dashboard import actionable_issue_rows, cell, display_value, e, empty_value_label, issue_rows, latest_position_rows, market_calendar_state, reason_summary, real_account_state, recommendation_shape_rows, recommendation_tracking_rows, recommendation_tracking_summary, render, sample_progress_rows, settings_rows, signed_class, sort_table_rows, status_class, table, today_issue_count, today_recommendation_rows, today_run_rows, today_sell_alert_rows, watch_state_pill_class, write


class DashboardTest(unittest.TestCase):
    def test_escape(self):
        self.assertEqual("&lt;x&gt;", e("<x>"))

    def test_table(self):
        html = table("T", [{"a": "1"}], ["a"])

        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)

    def test_table_sorts_visible_creation_time_newest_first(self):
        rows = [
            {"created_at": "2026-08-13T09:05:00", "name": "old"},
            {"created_at": "2026-08-13T15:35:00", "name": "new"},
        ]

        ordered = sort_table_rows(rows, ["created_at", "name"])
        html = table("T", rows, ["created_at", "name"])

        self.assertEqual(["new", "old"], [row["name"] for row in ordered])
        self.assertLess(html.index(">new</td>"), html.index(">old</td>"))

    def test_table_preserves_order_without_creation_time_column(self):
        rows = [{"name": "first"}, {"name": "second"}]
        self.assertEqual(rows, sort_table_rows(rows, ["name"]))

    def test_table_pager_shows_total_row_count(self):
        html = table("T", [{"name": str(index)} for index in range(19)], ["name"])
        self.assertIn("총 19건", html)

    @patch.dict(os.environ, {})
    @patch("stock_alarm.dashboard.today_recommendation_rows", return_value=[])
    def test_render_has_virtual_trader_and_five_page_pager(self, _recommendations):
        html = render()
        self.assertLess(html.index(">홈</label>"), html.index(">추천 추적</label>"))
        self.assertLess(html.index(">추천 추적</label>"), html.index(">가상 트레이더<"))
        self.assertLess(html.index(">가상 트레이더<"), html.index(">시스템 관리</label>"))
        self.assertIn("추천 추적 내역", html)
        self.assertIn("stockAlarm.virtualTrader.v1", html)
        self.assertIn("trader-total-equity", html)
        self.assertIn("item.average_price", html)
        self.assertIn("item.current_price", html)
        self.assertIn("Math.floor(currentPage/5)", html)

    @patch("stock_alarm.dashboard.recent_virtual_trades")
    @patch("stock_alarm.dashboard.latest_position_rows", return_value=[])
    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_tracking_separates_unbought_sell_alerts(self, tail_csv, _positions, trades):
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"pick_date": "2026-08-01", "ticker": "A", "name": "추천A", "score": "78.5", "entry_close": "100", "return_5d_pct": "3.5"}]
            if path.endswith("sell_alerts.csv"):
                return [{"created_at": "2026-08-10T15:40:00", "ticker": "A", "close": "102", "return_pct": "2.0", "reason": "20일선 이탈"}]
            return []

        tail_csv.side_effect = fake_tail
        trades.return_value = []

        rows = recommendation_tracking_rows()

        self.assertEqual("매도 알림", rows[0]["tracking_status"])
        self.assertEqual("미매수", rows[0]["virtual_bought"])
        self.assertEqual("2026-08-10", rows[0]["sell_alert_date"])
        self.assertEqual("102", rows[0]["sell_alert_price"])
        self.assertEqual("20일선 이탈", rows[0]["sell_reason"])
        self.assertEqual("78.5", rows[0]["score"])
        self.assertEqual("1종목", recommendation_tracking_summary(rows)[1][1])

    @patch("stock_alarm.dashboard.recent_virtual_trades", return_value=[])
    @patch("stock_alarm.dashboard.latest_position_rows")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_tracking_entry_price_matches_the_sell_alerts_basis(self, tail_csv, positions, _trades):
        # recommendation_performance.csv's entry_close (133000, a simulated
        # next-day-open price) is a different basis than the signal-day close
        # (113100) that positions.csv/sell_alerts.csv actually track against.
        # Showing 133000 next to a return computed against 113100 makes a
        # correct number look wrong.
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"pick_date": "2026-08-12", "ticker": "161890", "name": "한국콜마", "entry_close": "133000", "close": "113100"}]
            if path.endswith("sell_alerts.csv"):
                return [{"created_at": "2026-08-12T10:30:36", "ticker": "161890", "close": "134500", "return_pct": "18.92", "summary": "고점 대비 수익 반납"}]
            return []

        tail_csv.side_effect = fake_tail
        positions.return_value = [{"ticker": "161890", "entry_date": "2026-08-12", "entry_price": "113100", "close": "134500"}]

        row = recommendation_tracking_rows()[0]

        self.assertEqual("113100", row["entry_price"])
        self.assertEqual("134500", row["sell_alert_price"])
        self.assertEqual("18.92", row["sell_alert_return_pct"])

    @patch("stock_alarm.dashboard.recent_virtual_trades", return_value=[])
    @patch("stock_alarm.dashboard.latest_position_rows")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_tracking_does_not_borrow_a_newer_picks_position(self, tail_csv, positions, _trades):
        # A ticker recommended twice (once already sold, once currently held)
        # must not have the older, closed pick display the newer pick's
        # entry price/current price just because latest_position_rows()
        # only ever returns the ticker's one active position.
        def fake_tail(path, _count):
            if path.endswith("recommendations.csv"):
                return [
                    {"created_at": "2026-08-12T09:05:24", "ticker": "161890", "name": "한국콜마", "close": "113100"},
                    {"created_at": "2026-08-18T09:05:28", "ticker": "161890", "name": "한국콜마", "close": "127300"},
                ]
            if path.endswith("sell_alerts.csv"):
                return [{"created_at": "2026-08-12T10:30:36", "ticker": "161890", "close": "134500", "return_pct": "18.92"}]
            return []

        tail_csv.side_effect = fake_tail
        positions.return_value = [{"ticker": "161890", "entry_date": "2026-08-18", "entry_price": "127300", "close": "155000", "return_pct": "21.76"}]

        rows = {row["pick_date"]: row for row in recommendation_tracking_rows()}

        self.assertEqual("113100", rows["2026-08-12"]["entry_price"])
        self.assertEqual("134500", rows["2026-08-12"]["current_price"])
        self.assertEqual("127300", rows["2026-08-18"]["entry_price"])
        self.assertEqual("추적 중", rows["2026-08-18"]["tracking_status"])

    @patch("stock_alarm.dashboard.recent_virtual_trades", return_value=[])
    @patch("stock_alarm.dashboard.latest_position_rows", return_value=[])
    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_tracking_keeps_updating_after_a_sell_alert(self, tail_csv, _positions, _trades):
        # Once sold, 현재가/수익률 must keep tracking the stock via
        # recommendation_performance's ongoing Nd returns instead of freezing
        # at the sell alert's own price/return (which would just duplicate
        # sell_alert_price/sell_alert_return_pct forever).
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"pick_date": "2026-08-01", "ticker": "A", "name": "추천A", "entry_close": "100", "return_5d_pct": "3.5"}]
            if path.endswith("sell_alerts.csv"):
                return [{"created_at": "2026-08-03T15:40:00", "ticker": "A", "close": "102", "return_pct": "2.0"}]
            return []

        tail_csv.side_effect = fake_tail

        row = recommendation_tracking_rows()[0]

        self.assertEqual("매도 알림", row["tracking_status"])
        self.assertEqual("102", row["sell_alert_price"])
        self.assertEqual("2.0", row["sell_alert_return_pct"])
        self.assertEqual("3.5", row["return_pct"])
        self.assertEqual("103", row["current_price"])

    def test_status_class(self):
        self.assertEqual("ok", status_class("ok"))
        self.assertEqual("warn", status_class("old"))
        self.assertEqual("bad", status_class("missing"))
        self.assertEqual("", status_class("telegram"))

    def test_cell_marks_status(self):
        self.assertEqual("<td class='bad'>미실행</td>", cell("missing"))

    def test_numeric_cell_formats_and_aligns(self):
        self.assertEqual("<td class='num'>1,234,500</td>", cell("1234500", "close"))
        self.assertEqual("<td class='num'>12.35</td>", cell("12.345", "score"))
        self.assertEqual("<td class='num neg'>-10.30</td>", cell("-10.30", "return_1d_pct"))

    @patch("stock_alarm.dashboard.tail_csv")
    def test_sample_progress_rows_breaks_down_completion_by_horizon(self, tail_csv):
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [
                    {"pick_date": "2026-08-01", "ticker": "A", "return_1d_pct": "1.0", "return_3d_pct": "2.0"},
                    {"pick_date": "2026-08-02", "ticker": "B", "return_1d_pct": "0.5"},
                ]
            return []

        tail_csv.side_effect = fake_tail

        rows = {row["horizon"]: row for row in sample_progress_rows()}

        self.assertEqual("2", rows["1일"]["completed"])
        self.assertEqual("2", rows["1일"]["total"])
        self.assertEqual("100.0", rows["1일"]["percent"])
        self.assertEqual("1", rows["3일"]["completed"])
        self.assertEqual("50.0", rows["3일"]["percent"])
        self.assertEqual("0", rows["20일"]["completed"])

    @patch("stock_alarm.toss_client.TossClient")
    def test_market_calendar_state_parses_todays_regular_hours(self, toss_client_cls):
        toss_client_cls.return_value.market_calendar_kr.return_value = {
            "today": {"date": "2026-09-12", "integrated": {"regularMarket": {
                "startTime": "2026-09-12T09:00:00+09:00", "endTime": "2026-09-12T15:30:00+09:00",
            }}},
        }
        state = market_calendar_state()
        self.assertTrue(state["connected"])
        self.assertTrue(state["open"])
        self.assertEqual("09:00", state["start_time"])
        self.assertEqual("15:30", state["end_time"])

    @patch("stock_alarm.toss_client.TossClient")
    def test_market_calendar_state_handles_a_holiday(self, toss_client_cls):
        toss_client_cls.return_value.market_calendar_kr.return_value = {"today": {"date": "2026-09-13", "integrated": None}}
        state = market_calendar_state()
        self.assertTrue(state["connected"])
        self.assertFalse(state["open"])

    def test_real_account_state_reports_not_connected_without_credentials(self):
        with patch.dict("os.environ", {"TOSS_CLIENT_ID": "", "TOSS_CLIENT_SECRET": ""}):
            state = real_account_state()
        self.assertFalse(state["connected"])

    @patch("stock_alarm.toss_client.all_warnings_for", return_value={"LIQUIDATION_TRADING"})
    @patch("stock_alarm.toss_client.TossClient")
    def test_real_account_state_flags_a_holding_with_an_active_stock_warning(self, toss_client_cls, _warnings):
        toss_client_cls.return_value.accounts.return_value = [{"accountSeq": 1, "accountType": "BROKERAGE"}]
        toss_client_cls.return_value.holdings.return_value = {"items": [
            {"symbol": "005930", "name": "Samsung", "marketCountry": "KR", "quantity": 10,
             "averagePurchasePrice": 70000, "lastPrice": 71000,
             "marketValue": {"amount": 710000}, "profitLoss": {"amount": 10000, "rate": 0.014}},
        ]}
        toss_client_cls.return_value.buying_power.return_value = {"cashBuyingPower": "0"}

        state = real_account_state()

        self.assertEqual("종목 경고: LIQUIDATION_TRADING", state["holdings"][0]["watch_state"])

    @patch.dict("os.environ", {})
    @patch("stock_alarm.dashboard.today_recommendation_rows", return_value=[])
    @patch("stock_alarm.toss_client.all_warnings_for", return_value={"LIQUIDATION_TRADING"})
    @patch("stock_alarm.toss_client.TossClient")
    def test_nav_badge_shows_the_real_account_warning_count(self, toss_client_cls, _warnings, _recommendations):
        toss_client_cls.return_value.accounts.return_value = [{"accountSeq": 1, "accountType": "BROKERAGE"}]
        toss_client_cls.return_value.holdings.return_value = {"items": [
            {"symbol": "005930", "name": "Samsung", "marketCountry": "KR", "quantity": 10,
             "averagePurchasePrice": 70000, "lastPrice": 71000,
             "marketValue": {"amount": 710000}, "profitLoss": {"amount": 10000, "rate": 0.014}},
        ]}
        toss_client_cls.return_value.buying_power.return_value = {"cashBuyingPower": "0"}

        html = render()

        self.assertIn('실제 계좌<span class="nav-badge">1</span>', html)
        self.assertIn('id="nav-badge-trader" hidden', html)

    def test_watch_state_pill_class_matches_by_prefix_despite_the_ticker_suffix(self):
        self.assertEqual("pill-danger", watch_state_pill_class("종목 경고: LIQUIDATION_TRADING"))
        self.assertEqual("pill-warn", watch_state_pill_class("종목 주의: OVERHEATED"))
        self.assertEqual("pill-neutral", watch_state_pill_class("정상 보유"))
        self.assertEqual("", watch_state_pill_class("데이터 대기"))

    def test_watch_state_renders_as_a_pill(self):
        self.assertIn("status-pill pill-danger", cell("종목 경고: LIQUIDATION_TRADING", "watch_state"))
        self.assertIn("status-pill pill-warn", cell("종목 주의: OVERHEATED", "watch_state"))

    def test_tracking_status_renders_as_a_pill(self):
        self.assertIn("status-pill pill-accent", cell("추적 중", "tracking_status"))
        self.assertIn("status-pill pill-danger", cell("매도 알림", "tracking_status"))

    def test_tracking_status_badge_shows_a_simplified_label_with_the_raw_status_as_a_tooltip(self):
        self.assertIn("title='추적 중'", cell("추적 중", "tracking_status"))
        self.assertIn(">진행 중<", cell("추적 중", "tracking_status"))
        self.assertIn(">진행 중<", cell("성과 수집 중", "tracking_status"))
        self.assertIn("title='매도 알림'", cell("매도 알림", "tracking_status"))
        self.assertIn(">완료 (매도)<", cell("매도 알림", "tracking_status"))
        self.assertIn(">완료 (기간만료)<", cell("성과 완료", "tracking_status"))

    def test_price_quality_status_and_reason_render_in_korean(self):
        self.assertEqual("격리됨", display_value("quarantined"))
        self.assertEqual("지연됨", display_value("stale"))
        self.assertEqual("종가 불일치", display_value("close_source_mismatch"))
        self.assertEqual("기준일=2026-09-11", display_value("price_date=2026-09-11"))
        self.assertEqual("OHLC 이상값(경고:정리매매)", display_value("invalid_ohlc(경고:LIQUIDATION_TRADING)"))
        self.assertEqual("종목 경고:정리매매", display_value("stock_warning:LIQUIDATION_TRADING"))
        self.assertEqual("가격 지연됨:기준일=2026-09-11", display_value("price_stale:price_date=2026-09-11"))

    @patch.dict("os.environ", {"NEWS_LOOKUP": "1", "NEWS_SCORE_WEIGHT": "1"})
    def test_empty_value_labels_explain_pending_data(self):
        self.assertEqual("수집대기", empty_value_label("return_3d_pct"))
        self.assertEqual("수집대기", empty_value_label("news_score"))
        self.assertEqual("<td class='num'>수집대기</td>", cell("", "return_3d_pct"))

    @patch.dict("os.environ", {"NEWS_LOOKUP": "0", "NEWS_SCORE_WEIGHT": "0"})
    def test_news_score_label_shows_unused_when_disabled(self):
        self.assertEqual("미사용", empty_value_label("news_score"))

    def test_signed_class(self):
        self.assertEqual("pos", signed_class("1.2%"))
        self.assertEqual("neg", signed_class("-1.2"))
        self.assertEqual("zero", signed_class("0"))

    def test_table_marks_numeric_header(self):
        html = table("T", [{"score": "12.345"}], ["score"])

        self.assertIn("<th class='num'>", html)
        self.assertIn("<td class='num'>12.35</td>", html)

    def test_table_adds_pager_after_15_rows(self):
        html = table("T", [{"a": str(index)} for index in range(16)], ["a"])

        self.assertIn("data-page-size='15'", html)
        self.assertIn("data-row='15'", html)

    @patch("stock_alarm.dashboard.recent_virtual_trades", return_value=[])
    @patch("stock_alarm.dashboard.datetime")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_recommendation_rows(self, tail_csv, datetime, _trades):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-25"
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"ticker": "A", "news_score": "1"}]
            return [{"created_at": "2026-07-24T09:00:00"}, {"created_at": "2026-07-25T09:00:00", "ticker": "A"}]

        tail_csv.side_effect = fake_tail

        row = today_recommendation_rows()[0]
        self.assertEqual("A", row["ticker"])
        self.assertEqual("뉴스 보너스", row["reason"])

    @patch("stock_alarm.dashboard.recent_virtual_trades", return_value=[])
    @patch("stock_alarm.dashboard.datetime")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_recommendation_rows_accumulates_all_batches_for_the_day(self, tail_csv, datetime, _trades):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-27"

        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return []
            return [
                {"created_at": "2026-07-27T08:55:00", "ticker": "OLD1"},
                {"created_at": "2026-07-27T08:55:00", "ticker": "OLD2"},
                {"created_at": "2026-07-27T16:10:00", "ticker": "NEW1"},
                {"created_at": "2026-07-27T16:10:00", "ticker": "NEW2"},
            ]

        tail_csv.side_effect = fake_tail

        self.assertEqual(["OLD1", "OLD2", "NEW1", "NEW2"], [row["ticker"] for row in today_recommendation_rows()])

    @patch("stock_alarm.dashboard.datetime")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_recommendation_rows_shows_each_profiles_own_order_status(self, tail_csv, datetime):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-25"

        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return []
            return [{"created_at": "2026-07-25T09:00:00", "ticker": "A"}]

        tail_csv.side_effect = fake_tail

        def fake_trades(_limit, path="data/stock_alarm.db"):
            return [{"ticker": "A", "created_at": "2026-07-25T09:05:00"}] if path == "data/stock_alarm.db" else []

        with patch("stock_alarm.dashboard.recent_virtual_trades", side_effect=fake_trades):
            row = today_recommendation_rows()[0]

        self.assertEqual("체결", row["aggressive_order_status"])
        self.assertEqual("미체결", row["neutral_order_status"])

    @patch("stock_alarm.dashboard.actionable_issue_rows", return_value=[{"source": "텔레그램", "item": "알림 전송 실패", "status": "12회 반복"}])
    def test_today_issue_count(self, _issues):
        self.assertEqual(1, today_issue_count())

    @patch("stock_alarm.dashboard.latest_portfolio_risk")
    @patch("stock_alarm.dashboard.recent_price_quality", return_value=[])
    @patch("stock_alarm.dashboard.today_run_rows", return_value=[])
    @patch("stock_alarm.dashboard.today_delivery_failure_count", return_value=0)
    def test_actionable_issue_rows_flags_either_profiles_halt(self, _deliveries, _runs, _quality, risk):
        # A halt on the comparison-only neutral profile is still worth
        # surfacing -- only the real (aggressive) account halting silently
        # would be a much bigger problem to miss.
        def fake_risk(path):
            return {"status": "halted", "reason": "exposure_limit"} if path == "data/stock_alarm_neutral.db" else {"status": "active"}

        risk.side_effect = fake_risk

        rows = actionable_issue_rows()

        self.assertEqual(1, len(rows))
        self.assertIn("위험중립형", rows[0]["item"])

    @patch("stock_alarm.dashboard.run_log_statuses", return_value=["recommendations=ok", "positions_report=missing"])
    def test_today_run_rows(self, _statuses):
        self.assertEqual(
            [{"step": "recommendations", "status": "ok"}, {"step": "positions_report", "status": "missing"}],
            today_run_rows(),
        )

    @patch("stock_alarm.dashboard.actionable_issue_rows", return_value=[{"source": "텔레그램", "item": "알림 전송 실패", "status": "12회 반복 · 콘솔로 대체"}])
    def test_issue_rows(self, _issues):
        self.assertEqual(
            [{"source": "텔레그램", "item": "알림 전송 실패", "status": "12회 반복 · 콘솔로 대체"}],
            issue_rows(),
        )

    @patch("stock_alarm.dashboard.actionable_issue_rows", return_value=[])
    def test_issue_rows_shows_none(self, _issues):
        self.assertEqual([{"source": "대시보드", "item": "조치할 문제", "status": "없음"}], issue_rows())

    @patch("stock_alarm.dashboard.active_position_tickers", return_value={"A", "B"})
    @patch("stock_alarm.dashboard.tail_csv")
    def test_latest_position_rows_dedupes_ticker(self, tail_csv, _active):
        def fake_tail(path, _count):
            if path.endswith("sell_alerts.csv"):
                return []
            return [
                {"created_at": "2026-07-26T09:00:00", "ticker": "A", "return_pct": "0"},
                {"created_at": "2026-07-26T10:00:00", "ticker": "B", "return_pct": "1"},
                {"created_at": "2026-07-26T11:00:00", "ticker": "A", "return_pct": "-2"},
            ]

        tail_csv.side_effect = fake_tail

        rows = latest_position_rows()

        self.assertEqual(["A", "B"], [row["ticker"] for row in rows])
        self.assertEqual("-2", rows[0]["return_pct"])

    @patch("stock_alarm.dashboard.active_position_tickers", return_value={"B"})
    @patch("stock_alarm.dashboard.tail_csv")
    def test_latest_position_rows_skips_sell_alerted_ticker(self, tail_csv, _active):
        def fake_tail(path, _count):
            if path.endswith("sell_alerts.csv"):
                return [{"ticker": "A"}]
            return [
                {"created_at": "2026-07-26T10:00:00", "ticker": "B", "return_pct": "1"},
                {"created_at": "2026-07-26T11:00:00", "ticker": "A", "return_pct": "-2"},
            ]

        tail_csv.side_effect = fake_tail

        rows = latest_position_rows()

        self.assertEqual(["B"], [row["ticker"] for row in rows])

    @patch("stock_alarm.dashboard.position_was_alerted")
    @patch("stock_alarm.dashboard.active_position_tickers", return_value={"A"})
    @patch("stock_alarm.dashboard.tail_csv")
    def test_latest_position_rows_hides_alerted_entry_but_keeps_later_reentry(self, tail_csv, _active, was_alerted):
        tail_csv.return_value = [
            {"created_at": "2026-08-31T10:00:00", "ticker": "A", "entry_date": "2026-08-30", "position_id": "new"},
            {"created_at": "2026-08-31T10:00:00", "ticker": "A", "entry_date": "2026-08-20", "position_id": "old"},
        ]
        was_alerted.side_effect = lambda row: row["position_id"] == "old"

        rows = latest_position_rows()

        self.assertEqual(["new"], [row["position_id"] for row in rows])

    def test_reason_summary(self):
        self.assertEqual("기본 조건 충족", reason_summary({"volume_ratio": "1.5"}, {}, 0))

    def test_recommendation_shape_rows(self):
        rows = recommendation_shape_rows()

        self.assertEqual("관심 후보", rows[0]["type"])
        self.assertEqual("매도 검토", rows[-1]["type"])

    @patch("stock_alarm.dashboard.health_lines", return_value=["NEWS_SCORE_WEIGHT=2", "task_error=none"])
    def test_settings_rows(self, _health):
        self.assertEqual(
            [{"setting": "NEWS_SCORE_WEIGHT", "value": "2"}, {"setting": "task_error", "value": "none"}],
            settings_rows(),
        )

    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_sell_alert_rows_merges_profiles_into_one_row_per_ticker(self, tail_csv):
        today = datetime.now().date().isoformat()
        # notify=False (neutral) skips delivery reconciliation entirely, so it
        # needs no matching row in logs/deliveries.csv to show up here.
        rows_by_path = {
            "logs/sell_alerts.csv": [{"ticker": "005930", "created_at": f"{today}T09:00:00", "summary": "손절 -5.0% 이탈"}],
            "logs/sell_alerts_neutral.csv": [{"ticker": "005930", "created_at": f"{today}T09:05:00", "summary": "손절 -3.0% 이탈"}],
            "logs/deliveries.csv": [],
        }
        tail_csv.side_effect = lambda path, count: rows_by_path.get(path, [])

        rows = today_sell_alert_rows()

        self.assertEqual(1, len(rows))
        self.assertEqual("손절 -5.0% 이탈", rows[0]["aggressive_status"])
        self.assertEqual("손절 -3.0% 이탈", rows[0]["neutral_status"])

    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_sell_alert_rows_marks_untriggered_profile(self, tail_csv):
        today = datetime.now().date().isoformat()
        rows_by_path = {
            "logs/sell_alerts.csv": [{"ticker": "005930", "created_at": f"{today}T09:00:00", "summary": "손절"}],
            "logs/sell_alerts_neutral.csv": [],
            "logs/deliveries.csv": [],
        }
        tail_csv.side_effect = lambda path, count: rows_by_path.get(path, [])

        rows = today_sell_alert_rows()

        self.assertEqual("-", rows[0]["neutral_status"])

    @patch("stock_alarm.dashboard.daily_check_lines", return_value=["daily ok"])
    @patch("stock_alarm.dashboard.issue_rows", return_value=[])
    @patch("stock_alarm.dashboard.settings_rows", return_value=[])
    @patch("stock_alarm.dashboard.today_run_rows", return_value=[])
    @patch("stock_alarm.dashboard.tail_text", return_value=[])
    @patch("stock_alarm.dashboard.tail_csv", return_value=[])
    def test_render(self, _csv, _text, _run_rows, _settings, _issues, _daily):
        html = render()

        self.assertIn("국내주식 알림 대시보드", html)
        self.assertIn('class="dashboard-header"', html)
        self.assertIn('class="dashboard-meta"', html)
        self.assertRegex(html, r"생성 시각 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        self.assertIn("오늘의 투자 현황", html)
        self.assertIn("가상계좌 성향 비교", html)
        self.assertIn("보유종목 총수익률", html)
        self.assertIn("시스템 관리", html)
        self.assertIn("문제", html)
        self.assertIn("일일 점검", html)
        self.assertIn("현재 설정", html)
        self.assertIn("최근 발송", html)
        self.assertIn("오늘 실행 상세", html)
        self.assertIn("오늘 추천 종목", html)
        self.assertIn("추천 형태", html)
        self.assertIn("데이터 축적 현황", html)
        self.assertIn("현재 운영 상태", html)
        self.assertIn("알고리즘 검증 결과", html)
        self.assertIn("데이터 학습 준비", html)
        self.assertIn("현재 항목이 없습니다", html)
        self.assertIn("시장 모드", html)
        self.assertIn("매도 내역", html)
        self.assertIn("누적 실현손익", html)
        self.assertIn("가상계좌 자산 구성", html)
        self.assertIn("보유종목 업종 비중", html)
        self.assertIn("conic-gradient", html)
        self.assertIn("renderPortfolioCharts()", html)
        self.assertIn("item.sale_label", html)
        self.assertIn("Math.floor(page/5)*5", html)
        self.assertNotIn("메시지 ID</th>", html)
        self.assertNotIn("설정 해시</th>", html)
        self.assertLess(html.index("문제"), html.index("오늘 실행 상세"))

    @patch("stock_alarm.dashboard.render", return_value="<html></html>")
    def test_write(self, _render):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dashboard.html")

            self.assertEqual(path, write(path))
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
