import email.utils
import json
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from stock_alarm.point_in_time_collect import PartialCollectionError, _parse_report_period, collect, collect_financial_statements, collect_news, collect_stock_warnings
from stock_alarm.point_in_time_store import connect


class PointInTimeCollectTest(unittest.TestCase):
    def test_parse_report_period_maps_filing_month_to_reprt_code(self):
        self.assertEqual((2023, "11011"), _parse_report_period("사업보고서 (2023.12)"))
        self.assertEqual((2024, "11012"), _parse_report_period("반기보고서 (2024.06)"))
        self.assertEqual((2024, "11013"), _parse_report_period("분기보고서 (2024.03)"))
        self.assertEqual((2024, "11014"), _parse_report_period("분기보고서 (2024.09)"))
        self.assertEqual((2023, "11011"), _parse_report_period("[기재정정]사업보고서 (2023.12)"))
        self.assertIsNone(_parse_report_period("주요사항보고서"))

    def test_collect_financial_statements_reads_matching_disclosures_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "pit.sqlite3")) as db:
            rows = [
                ("005930", "00126380", "1", "사업보고서 (2023.12)", "2024-03-15T00:00:00+00:00", "2024-03-16T00:00:00+00:00", "date_only_next_business_day", "now", "{}"),
                ("005930", "00126380", "2", "[기재정정]사업보고서 (2023.12)", "2024-03-20T00:00:00+00:00", "2024-03-21T00:00:00+00:00", "date_only_next_business_day", "now", "{}"),
                ("005930", "00126380", "3", "주요사항보고서", "2024-03-25T00:00:00+00:00", "2024-03-26T00:00:00+00:00", "date_only_next_business_day", "now", "{}"),
            ]
            db.executemany("INSERT INTO disclosure_events VALUES(?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
            with patch("stock_alarm.point_in_time_collect.time.sleep"), \
                 patch("stock_alarm.financial_statement_reference.fetch_account_summary", return_value={
                     "revenue": 1000.0, "revenue_prev": 800.0, "operating_income": 100.0, "operating_income_prev": 50.0,
                     "net_income": 80.0, "equity": 400.0, "liabilities": 200.0,
                 }) as fetch:
                records = collect_financial_statements("005930", date(2022, 1, 1), date(2026, 1, 1), db)

            self.assertEqual(1, records)
            fetch.assert_called_once_with("005930", year=2023, reprt_code="11011")
            saved = db.execute("SELECT bsns_year, reprt_code, roe_pct, published_at FROM financial_statement_snapshots").fetchall()
            # The correction ("2", published 2024-03-20) must win over the
            # original filing ("1", published 2024-03-15).
            self.assertEqual([(2023, "11011", 20.0, "2024-03-20T00:00:00+00:00")], [tuple(row) for row in saved])
    def test_collect_uses_the_static_watchlist_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("stock_alarm.point_in_time_collect.load_env"), \
             patch("stock_alarm.point_in_time_collect.configured_stocks", return_value={"005930": "Samsung"}), \
             patch("stock_alarm.point_in_time_collect.collect_disclosures", return_value=0) as collect_disclosures:
            collect(date(2024, 1, 1), date(2024, 1, 2), Path(tmp) / "pit.sqlite3", sources=("disclosure",))
        collect_disclosures.assert_called_once()
        self.assertEqual("005930", collect_disclosures.call_args.args[0])

    def test_collect_widens_to_the_live_dynamic_universe_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("stock_alarm.point_in_time_collect.load_env"), \
             patch("stock_alarm.app.recommend_universe", return_value={"000660": "SK hynix"}), \
             patch("stock_alarm.point_in_time_collect.collect_disclosures", return_value=0) as collect_disclosures:
            collect(date(2024, 1, 1), date(2024, 1, 2), Path(tmp) / "pit.sqlite3", sources=("disclosure",), use_dynamic_universe=True)
        collect_disclosures.assert_called_once()
        self.assertEqual("000660", collect_disclosures.call_args.args[0])

    def test_news_limit_is_reported_as_partial_when_start_date_is_not_reached(self):
        published = email.utils.format_datetime(datetime(2024, 6, 1, 9, tzinfo=timezone.utc))
        payloads = []
        for page in range(10):
            items = [
                {"title": f"news-{page}-{index}", "pubDate": published, "link": f"https://example.com/{page}/{index}"}
                for index in range(100)
            ]
            payloads.append(json.dumps({"total": 5000, "items": items}).encode("utf-8"))

        with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "pit.sqlite3")) as db, \
             patch.dict("os.environ", {"NAVER_HUB_CLIENT_ID": "id", "NAVER_HUB_CLIENT_SECRET": "secret"}), \
             patch("stock_alarm.point_in_time_collect.time.sleep"), \
             patch("stock_alarm.point_in_time_collect.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.side_effect = payloads
            with self.assertRaises(PartialCollectionError) as caught:
                collect_news("005930", "삼성전자", date(2022, 6, 30), date(2026, 1, 1), db)

        self.assertEqual(1000, caught.exception.records)
        self.assertEqual(date(2024, 6, 1), caught.exception.coverage_start)
        self.assertIn("requested start 2022-06-30 was not reached", str(caught.exception))

    def test_collect_dispatches_the_stock_warning_source(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("stock_alarm.point_in_time_collect.load_env"), \
             patch("stock_alarm.point_in_time_collect.configured_stocks", return_value={"005930": "Samsung"}), \
             patch("stock_alarm.point_in_time_collect.collect_stock_warnings", return_value=0) as collect_stock_warnings_mock:
            collect(date(2024, 1, 1), date(2024, 1, 2), Path(tmp) / "pit.sqlite3", sources=("stock_warning",))
        collect_stock_warnings_mock.assert_called_once()
        self.assertEqual("005930", collect_stock_warnings_mock.call_args.args[0])

    @patch("stock_alarm.toss_client.shared_client")
    def test_collect_stock_warnings_stores_active_warnings(self, shared_client):
        shared_client.return_value.stock_warnings.return_value = [
            {"warningType": "LIQUIDATION_TRADING", "startDate": "2026-09-01", "endDate": None},
        ]
        with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "pit.sqlite3")) as db:
            records = collect_stock_warnings("033340", db)
            stored = db.execute("SELECT ticker, warning_type, start_date FROM stock_warning_snapshots").fetchall()

        self.assertEqual(1, records)
        self.assertEqual([("033340", "LIQUIDATION_TRADING", "2026-09-01")], [tuple(row) for row in stored])

    @patch("stock_alarm.toss_client.shared_client")
    def test_collect_stock_warnings_preserves_a_toss_failure(self, shared_client):
        shared_client.side_effect = ValueError("missing credentials")
        with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "pit.sqlite3")) as db:
            with self.assertRaisesRegex(ValueError, "missing credentials"):
                collect_stock_warnings("033340", db)


if __name__ == "__main__":
    unittest.main()
