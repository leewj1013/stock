import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.dashboard import card, card_class, cell, e, issue_rows, latest_position_rows, latest_recommendation_rows, performance_penalty_rows, performance_summary_rows, reason_summary, recommendation_performance_rows, recommendation_rank_rows, recommendation_reason_rows, recommendation_shape_rows, render, score_breakdown_rows, sell_alert_summary_rows, sell_alerted_recommendation_rows, settings_rows, signed_class, status_class, table, today_csv_count, today_issue_count, today_recommendation_rows, today_run_rows, today_run_summary, trading_value_eok, write


class DashboardTest(unittest.TestCase):
    def test_escape(self):
        self.assertEqual("&lt;x&gt;", e("<x>"))

    def test_table(self):
        html = table("T", [{"a": "1"}], ["a"])

        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)

    def test_status_class(self):
        self.assertEqual("ok", status_class("ok"))
        self.assertEqual("warn", status_class("old"))
        self.assertEqual("bad", status_class("missing"))
        self.assertEqual("", status_class("telegram"))

    def test_cell_marks_status(self):
        self.assertEqual("<td class='bad'>missing</td>", cell("missing"))

    def test_numeric_cell_formats_and_aligns(self):
        self.assertEqual("<td class='num'>1,234,500</td>", cell("1234500", "close"))
        self.assertEqual("<td class='num'>12.35</td>", cell("12.345", "score"))
        self.assertEqual("<td class='num neg'>-10.30</td>", cell("-10.30", "return_1d_pct"))

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

    def test_card_marks_status(self):
        self.assertIn("<span class='bad'>missing</span>", card("latest error", "missing"))

    def test_card_class_marks_today_counts(self):
        self.assertEqual("bad", card_class("today issues", "1"))
        self.assertEqual("ok", card_class("today issues", "0"))
        self.assertEqual("muted", card_class("today recommendations", "0"))
        self.assertEqual("ok", card_class("today recommendations", "2"))

    @patch("stock_alarm.dashboard.latest_error_summary", return_value="none")
    @patch("stock_alarm.dashboard.position_count", return_value=1)
    @patch("stock_alarm.dashboard.today_issue_count", return_value=2)
    @patch("stock_alarm.dashboard.tail_csv")
    def test_metric_cards_include_latest_error(self, tail_csv, _issues, _positions, _error):
        tail_csv.side_effect = [
            [{"metric": "rows", "value": "3"}],
            [{"created_at": "2026-07-25T09:00:00", "channel": "telegram"}],
            [{"created_at": "2026-07-25T09:00:00"}],
            [{"created_at": "2026-07-25T10:00:00"}],
        ]

        from stock_alarm.dashboard import metric_cards

        cards = metric_cards()
        self.assertIn(("latest error", "none"), cards)
        self.assertIn(("today issues", "2"), cards)

    @patch("stock_alarm.dashboard.datetime")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_csv_count(self, tail_csv, datetime):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-25"
        tail_csv.return_value = [{"created_at": "2026-07-25T09:00:00"}, {"created_at": "2026-07-24T09:00:00"}]

        self.assertEqual(1, today_csv_count("logs/recommendations.csv"))

    @patch("stock_alarm.dashboard.datetime")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_recommendation_rows(self, tail_csv, datetime):
        datetime.now.return_value.date.return_value.isoformat.return_value = "2026-07-25"
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"ticker": "A", "news_score": "1"}]
            return [{"created_at": "2026-07-24T09:00:00"}, {"created_at": "2026-07-25T09:00:00", "ticker": "A"}]

        tail_csv.side_effect = fake_tail

        row = today_recommendation_rows()[0]
        self.assertEqual("A", row["ticker"])
        self.assertEqual("뉴스 보너스", row["reason"])

    @patch("stock_alarm.dashboard.datetime")
    @patch("stock_alarm.dashboard.tail_csv")
    def test_today_recommendation_rows_shows_latest_batch_only(self, tail_csv, datetime):
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

        self.assertEqual(["NEW2", "NEW1"], [row["ticker"] for row in today_recommendation_rows()])

    @patch("stock_alarm.dashboard.today_run_rows", return_value=[{"step": "daily", "status": "missing"}])
    @patch("stock_alarm.dashboard.settings_rows", return_value=[{"setting": "task_error", "value": "none"}])
    def test_today_issue_count(self, _settings, _runs):
        self.assertEqual(1, today_issue_count())

    @patch("stock_alarm.dashboard.run_log_statuses", return_value=["recommendations=ok", "sell_check=missing", "dashboard=ok"])
    def test_today_run_summary(self, _statuses):
        self.assertEqual("2/3 ok", today_run_summary())

    @patch("stock_alarm.dashboard.run_log_statuses", return_value=["recommendations=ok", "positions_report=missing"])
    def test_today_run_rows(self, _statuses):
        self.assertEqual(
            [{"step": "recommendations", "status": "ok"}, {"step": "positions_report", "status": "missing"}],
            today_run_rows(),
        )

    @patch("stock_alarm.dashboard.today_run_rows", return_value=[{"step": "positions_report", "status": "missing"}])
    @patch("stock_alarm.dashboard.settings_rows", return_value=[{"setting": "DART_API_KEY", "value": "missing"}])
    @patch("stock_alarm.dashboard.metric_cards", return_value=[("latest error", "none")])
    def test_issue_rows(self, _cards, _settings, _runs):
        self.assertEqual(
            [
                {"source": "setting", "item": "DART_API_KEY", "status": "missing"},
                {"source": "run", "item": "positions_report", "status": "missing"},
            ],
            issue_rows(),
        )

    @patch("stock_alarm.dashboard.today_run_rows", return_value=[])
    @patch("stock_alarm.dashboard.settings_rows", return_value=[])
    @patch("stock_alarm.dashboard.metric_cards", return_value=[("latest error", "none")])
    def test_issue_rows_shows_none(self, _cards, _settings, _runs):
        self.assertEqual([{"source": "dashboard", "item": "issues", "status": "none"}], issue_rows())

    @patch("stock_alarm.dashboard.tail_csv")
    def test_performance_summary_rows(self, tail_csv):
        tail_csv.return_value = [
            {"metric": "avg_1d_return_pct", "value": "1.23"},
            {"metric": "win_rate_1d_pct", "value": "60.0"},
            {"metric": "suggested_min_score", "value": "70"},
        ]

        rows = performance_summary_rows()

        self.assertIn({"metric": "전체 1일 평균", "value": "1.23%"}, rows)
        self.assertIn({"metric": "전체 1일 승률", "value": "60.0%"}, rows)
        self.assertIn({"metric": "추천 최소점수 제안", "value": "70"}, rows)

    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_rank_rows(self, tail_csv):
        tail_csv.return_value = [
            {"ticker": "000001", "name": "A", "return_1d_pct": "1.00"},
            {"ticker": "000001", "name": "A", "return_1d_pct": "-2.00"},
            {"ticker": "000002", "name": "B", "return_1d_pct": "3.00"},
            {"ticker": "000003", "name": "C", "return_1d_pct": ""},
        ]

        rows = recommendation_rank_rows()

        self.assertEqual("000002", rows[0]["ticker"])
        self.assertEqual("1", rows[0]["picks"])
        self.assertEqual("3.00", rows[0]["avg_1d_return_pct"])
        self.assertEqual("50.0", rows[1]["win_rate_1d_pct"])

        self.assertEqual("000001", recommendation_rank_rows(worst=True)[0]["ticker"])

    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_performance_rows_averages_duplicates(self, tail_csv):
        tail_csv.return_value = [
            {"pick_date": "2026-07-25", "ticker": "000001", "name": "A", "score": "80", "entry_close": "1000", "return_1d_pct": "2.00", "news_score": "1"},
            {"pick_date": "2026-07-26", "ticker": "000001", "name": "A", "score": "60", "entry_close": "1100", "return_1d_pct": "-4.00", "news_score": "3"},
            {"pick_date": "2026-07-26", "ticker": "000002", "name": "B", "score": "90", "entry_close": "2000", "return_1d_pct": "5.00"},
        ]

        rows = recommendation_performance_rows()

        self.assertEqual("000001", rows[0]["ticker"])
        self.assertEqual("2", rows[0]["entry_count"])
        self.assertEqual("70.00", rows[0]["score"])
        self.assertEqual("1050", rows[0]["entry_close"])
        self.assertEqual("-1.00", rows[0]["return_1d_pct"])

    @patch("stock_alarm.dashboard.tail_csv")
    def test_latest_recommendation_rows_dedupes_ticker(self, tail_csv):
        tail_csv.return_value = [
            {"created_at": "2026-07-26T09:00:00", "ticker": "A", "score": "50"},
            {"created_at": "2026-07-26T10:00:00", "ticker": "B", "score": "60"},
            {"created_at": "2026-07-26T11:00:00", "ticker": "A", "score": "70"},
        ]

        rows = latest_recommendation_rows()

        self.assertEqual(["A", "B"], [row["ticker"] for row in rows])
        self.assertEqual("70", rows[0]["score"])

    @patch("stock_alarm.dashboard.tail_csv")
    def test_latest_position_rows_dedupes_ticker(self, tail_csv):
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

    @patch("stock_alarm.dashboard.tail_csv")
    def test_latest_position_rows_skips_sell_alerted_ticker(self, tail_csv):
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

    @patch("stock_alarm.dashboard.tail_csv")
    def test_sell_alerted_recommendation_rows(self, tail_csv):
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"ticker": "000001", "name": "A", "score": "80", "entry_close": "1000", "return_1d_pct": "-1.00"}]
            return [{"ticker": "000001", "name": "A", "return_pct": "-5.00", "reason": "stop loss"}]

        tail_csv.side_effect = fake_tail

        rows = sell_alerted_recommendation_rows()

        self.assertEqual(
            {
                "ticker": "000001",
                "name": "A",
                "score": "80",
                "entry_close": "1000",
                "return_1d_pct": "-1.00",
                "sell_return_pct": "-5.00",
                "sell_reason": "stop loss",
            },
            rows[0],
        )

    @patch("stock_alarm.dashboard.tail_csv")
    def test_sell_alert_summary_rows(self, tail_csv):
        tail_csv.return_value = [
            {"summary": "손실 -6.0%", "reason": "old"},
            {"summary": "손실 -6.0%", "reason": "old"},
            {"summary": "", "reason": "20일선 이탈"},
        ]

        self.assertEqual(
            [{"summary": "손실 -6.0%", "count": "2"}, {"summary": "20일선 이탈", "count": "1"}],
            sell_alert_summary_rows(),
        )

    @patch("stock_alarm.dashboard.performance_penalty", return_value=4)
    @patch("stock_alarm.dashboard.tail_csv")
    def test_performance_penalty_rows(self, tail_csv, _penalty):
        tail_csv.return_value = [
            {"ticker": "000001", "name": "A"},
            {"ticker": "000001", "name": "A"},
        ]

        self.assertEqual([{"ticker": "000001", "name": "A", "penalty": "4.00"}], performance_penalty_rows())

    @patch("stock_alarm.dashboard.performance_penalty", return_value=1.5)
    @patch("stock_alarm.dashboard.tail_csv")
    def test_recommendation_reason_rows(self, tail_csv, _penalty):
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"ticker": "000001", "news_score": "2.00", "disclosure_score": "3.00"}]
            return [
                {
                    "created_at": "2026-07-25T09:00:00",
                    "ticker": "000001",
                    "name": "A",
                    "score": "80",
                    "volume_ratio": "2.50",
                    "trading_value": "12300000000",
                }
            ]

        tail_csv.side_effect = fake_tail

        self.assertEqual(
            [
                {
                    "created_at": "2026-07-25T09:00:00",
                    "ticker": "000001",
                    "name": "A",
                    "score": "80",
                    "reason": "거래량 급증 + 뉴스 보너스 + 공시 보너스 + 성과 감점",
                    "volume_ratio": "2.50",
                    "trading_value_억": "123",
                    "news_score": "2.00",
                    "disclosure_score": "3.00",
                    "performance_penalty": "1.50",
                }
            ],
            recommendation_reason_rows(),
        )

    def test_reason_summary(self):
        self.assertEqual("기본 조건 충족", reason_summary({"volume_ratio": "1.5"}, {}, 0))

    def test_recommendation_shape_rows(self):
        rows = recommendation_shape_rows()

        self.assertEqual("관심 후보", rows[0]["type"])
        self.assertEqual("매도 검토", rows[-1]["type"])

    @patch("stock_alarm.dashboard.tail_csv")
    def test_score_breakdown_rows(self, tail_csv):
        def fake_tail(path, _count):
            if path.endswith("recommendation_performance.csv"):
                return [{"ticker": "000001", "news_score": "2", "disclosure_score": "3"}]
            return [
                {"ticker": "000001", "name": "A", "score": "70", "volume_score": "30", "trading_value_score": "20", "trend_score": "20"}
            ]

        tail_csv.side_effect = fake_tail

        row = score_breakdown_rows()[0]
        self.assertEqual("70", row["total_score"])
        self.assertEqual("2", row["news"])
        self.assertEqual("3", row["disclosure"])

    def test_trading_value_eok(self):
        self.assertEqual("123", trading_value_eok("12300000000"))
        self.assertEqual("", trading_value_eok("bad"))

    @patch("stock_alarm.dashboard.health_lines", return_value=["NEWS_SCORE_WEIGHT=2", "task_error=none"])
    def test_settings_rows(self, _health):
        self.assertEqual(
            [{"setting": "NEWS_SCORE_WEIGHT", "value": "2"}, {"setting": "task_error", "value": "none"}],
            settings_rows(),
        )

    @patch("stock_alarm.dashboard.daily_check_lines", return_value=["daily ok"])
    @patch("stock_alarm.dashboard.metric_cards", return_value=[("positions", "1")])
    @patch("stock_alarm.dashboard.issue_rows", return_value=[])
    @patch("stock_alarm.dashboard.settings_rows", return_value=[])
    @patch("stock_alarm.dashboard.today_run_rows", return_value=[])
    @patch("stock_alarm.dashboard.performance_summary_rows", return_value=[])
    @patch("stock_alarm.dashboard.recommendation_rank_rows", return_value=[])
    @patch("stock_alarm.dashboard.performance_penalty_rows", return_value=[])
    @patch("stock_alarm.dashboard.sell_alerted_recommendation_rows", return_value=[])
    @patch("stock_alarm.dashboard.tail_text", return_value=[])
    @patch("stock_alarm.dashboard.tail_csv", return_value=[])
    def test_render(self, _csv, _text, _sell, _penalties, _rank, _summary, _run_rows, _settings, _issues, _cards, _daily):
        html = render()

        self.assertIn("국내주식 알림 대시보드", html)
        self.assertIn("문제", html)
        self.assertIn("일일 점검", html)
        self.assertIn("현재 설정", html)
        self.assertIn("최근 발송", html)
        self.assertIn("오늘 실행 상세", html)
        self.assertIn("오늘 추천 종목", html)
        self.assertIn("추천 형태", html)
        self.assertIn("점수 구성", html)
        self.assertIn("공시", html)
        self.assertIn("추천 통계", html)
        self.assertIn("추천 성과 상위", html)
        self.assertIn("추천 성과 하위", html)
        self.assertIn("성과 감점", html)
        self.assertIn("매도 검토 연결 추천", html)
        self.assertIn("매도 검토 요약", html)
        self.assertIn("추천 사유", html)
        self.assertLess(html.index("문제"), html.index("오늘 실행 상세"))
        self.assertLess(html.index("오늘 실행 상세"), html.index("오늘 추천 종목"))
        self.assertLess(html.index("오늘 추천 종목"), html.index("추천 형태"))
        self.assertLess(html.index("추천 형태"), html.index("점수 구성"))
        self.assertLess(html.index("점수 구성"), html.index("추천 사유"))
        self.assertLess(html.index("추천 사유"), html.index("매도 검토 요약"))
        self.assertLess(html.index("매도 검토 요약"), html.index("최근 매도 검토"))
        self.assertLess(html.index("최근 매도 검토"), html.index("추천 통계"))

    @patch("stock_alarm.dashboard.render", return_value="<html></html>")
    def test_write(self, _render):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "dashboard.html")

            self.assertEqual(path, write(path))
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
