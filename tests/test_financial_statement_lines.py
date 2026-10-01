import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_alarm.financial_statement_lines import (
    DartQuotaExceeded,
    annual_periods,
    collect_ticker,
    collected_tickers,
    fetch_report,
    report_periods,
    store_lines,
)
from stock_alarm.point_in_time_store import connect
from stock_alarm.financial_statement_lines import SCHEMA


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


ROW = '{"status":"000","list":[{"sj_div":"IS","account_id":"ifrs-full_Revenue","account_nm":"매출액","thstrm_amount":"1,000","frmtrm_amount":"900","ord":"1","currency":"KRW"}]}'


class FinancialStatementLinesTest(unittest.TestCase):
    def test_report_periods_returns_most_recent_first(self):
        periods = report_periods(4, today=date(2026, 9, 16))
        self.assertEqual([(2026, "11011"), (2026, "11014"), (2026, "11012"), (2026, "11013")], periods)

    def test_annual_periods_cover_last_year_back_to_the_start(self):
        self.assertEqual([(2025, "11011"), (2024, "11011"), (2023, "11011")], annual_periods(2023, today=date(2026, 10, 1)))

    def test_skip_stored_only_fetches_missing_periods(self):
        with tempfile.TemporaryDirectory() as directory:
            with closing(connect(Path(directory) / "pit.sqlite3")) as db:
                db.executescript(SCHEMA)
                with patch("stock_alarm.financial_statement_lines.corp_code_by_stock", return_value="00126380"),                      patch("urllib.request.urlopen", return_value=FakeResponse(ROW)) as urlopen:
                    collect_ticker("005930", [(2025, "11011")], db, "key", delay=0)
                    urlopen.reset_mock()
                    result = collect_ticker("005930", [(2025, "11011"), (2024, "11011")], db, "key", delay=0, skip_stored=True)
        self.assertEqual(1, urlopen.call_count)  # only 2024 was fetched
        self.assertEqual(1, result["periods"])

    def test_quota_status_raises_instead_of_silently_returning_nothing(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse('{"status":"020","message":"limit"}')):
            with self.assertRaises(DartQuotaExceeded):
                fetch_report("00126380", "key", 2026, "11012")

    def test_no_data_status_returns_empty_without_raising(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse('{"status":"013","message":"no data"}')):
            self.assertEqual([], fetch_report("00126380", "key", 2026, "11012"))

    def test_collect_ticker_stores_parsed_amounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pit.sqlite3"
            with closing(connect(path)) as db:
                db.executescript(SCHEMA)
                with patch("stock_alarm.financial_statement_lines.corp_code_by_stock", return_value="00126380"), \
                     patch("urllib.request.urlopen", return_value=FakeResponse(ROW)):
                    result = collect_ticker("005930", [(2026, "11012")], db, "key", delay=0)
                self.assertEqual({"ticker": "005930", "status": "success", "periods": 1, "rows": 1}, result)
                row = db.execute("SELECT account_nm, thstrm_amount, frmtrm_amount, fs_div FROM financial_statement_lines").fetchone()
            self.assertEqual(("매출액", 1000.0, 900.0, "CFS"), tuple(row))

    def test_collected_tickers_lets_a_rerun_skip_finished_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pit.sqlite3"
            self.assertEqual(set(), collected_tickers(path))
            with closing(connect(path)) as db:
                db.executescript(SCHEMA)
                with patch("stock_alarm.financial_statement_lines.corp_code_by_stock", return_value="00126380"),                      patch("urllib.request.urlopen", return_value=FakeResponse(ROW)):
                    collect_ticker("005930", [(2026, "11012")], db, "key", delay=0)
            self.assertEqual({"005930"}, collected_tickers(path))

    def test_missing_corp_code_is_reported_not_raised(self):
        with patch("stock_alarm.financial_statement_lines.corp_code_by_stock", return_value=""):
            result = collect_ticker("999999", [(2026, "11012")], None, "key", delay=0)
        self.assertEqual("no_corp_code", result["status"])


if __name__ == "__main__":
    unittest.main()
