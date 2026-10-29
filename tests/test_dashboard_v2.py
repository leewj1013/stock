import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stock_alarm.dashboard_v2 import _cached_signal_history, _comparison_chart, _history_source_signature, _kr_account, _kr_system, _signal_history, _us_experiments, _us_system, dashboard_v2_payload, real_account_payload
from stock_alarm.dashboard_v2_server import DashboardV2Handler
from stock_alarm.us_dashboard import read_csv


class DashboardV2DataTest(unittest.TestCase):
    def test_csv_reader_accepts_bom_before_event_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.csv"
            path.write_text("created_at,ticker\n2026-10-22T14:02:29,476830\n", encoding="utf-8-sig")
            self.assertEqual("2026-10-22T14:02:29", read_csv(path)[0]["created_at"])

    @patch("stock_alarm.dashboard_v2.real_account_state")
    def test_real_account_exposes_only_balance_and_holdings(self, state):
        state.return_value = {"connected": True, "account_seq": "private", "cash": 200, "market_value": 800,
                              "profit_loss": 50, "total_equity": 1000, "orders": [{"id": "private"}],
                              "holdings": [{"ticker": "005930", "name": "삼성전자", "quantity": 2,
                                            "average_price": 300, "current_price": 400, "valuation": 800,
                                            "profit_loss": 50, "return_pct": "6.67", "watch_state": "정상 보유"}]}
        payload = real_account_payload()
        self.assertTrue(payload["connected"])
        self.assertEqual("₩1,000", payload["asset"])
        self.assertEqual("삼성전자 (005930)", payload["rows"][0][0])
        self.assertEqual("+6.67%", payload["returnPct"])
        self.assertNotIn("orders", payload)
        self.assertNotIn("account_seq", payload)

    @patch("stock_alarm.dashboard_v2.real_account_state", return_value={"connected": False, "reason": "TOSS_CLIENT_ID and TOSS_CLIENT_SECRET are required"})
    def test_real_account_missing_credentials_is_safe(self, _state):
        payload = real_account_payload()
        self.assertFalse(payload["connected"])
        self.assertNotIn("TOSS_CLIENT_SECRET", payload["reason"])

    @patch("stock_alarm.dashboard_v2.Path.exists", return_value=True)
    @patch("stock_alarm.dashboard_v2.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.dashboard_v2.recent_price_quality", return_value=[])
    @patch("stock_alarm.dashboard_v2.recent_position_checks")
    @patch("stock_alarm.dashboard_v2.query_rows")
    @patch("stock_alarm.dashboard_v2.virtual_trader_state")
    def test_kr_account_uses_saved_valuation_instead_of_cost_basis(self, state, snapshots, checks, _quality, _sales, _exists):
        state.return_value = {"total_equity": 1000, "deposited": 1000, "realized_profit_loss": 0, "holdings": [{"name": "테스트", "ticker": "000001", "quantity": 2, "cost": 1000, "average_price": 500, "first_entry_at": "2026-10-21T09:00:00"}]}
        snapshots.return_value = [{"created_at": "2026-10-22T15:30:00", "cash": 100, "valuation": 1100, "equity": 1200, "profit_loss": 100}, {"equity": 1180}]
        checks.return_value = []
        account = _kr_account("aggressive", "계좌")
        self.assertEqual("₩1,200", account["asset"])
        self.assertEqual("₩+20", account["daily"])
        self.assertEqual("+20.00%", account["returnPct"])
        self.assertEqual("기록 없음", account["rows"][0][5])
        self.assertEqual("기록 없음", account["rows"][0][6])
        self.assertEqual("기록 없음", account["rows"][0][7])

    @patch("stock_alarm.dashboard_v2.Path.exists", return_value=True)
    @patch("stock_alarm.dashboard_v2.recent_virtual_sales")
    @patch("stock_alarm.dashboard_v2.recent_price_quality", return_value=[])
    @patch("stock_alarm.dashboard_v2.recent_position_checks")
    @patch("stock_alarm.dashboard_v2.query_rows")
    @patch("stock_alarm.dashboard_v2.virtual_trader_state")
    def test_kr_account_holding_details_use_latest_saved_check(self, state, snapshots, checks, _quality, sales, _exists):
        state.return_value = {"deposited": 1000, "realized_profit_loss": 30, "holdings": [{"name": "테스트", "ticker": "000001", "quantity": 2, "cost": 1000, "average_price": 500, "first_entry_at": "2026-10-21T09:00:00", "position_status": "open"}]}
        snapshots.return_value = [{"created_at": "2026-10-22T15:30:00", "cash": 100, "valuation": 1100, "equity": 1200, "profit_loss": 100}]
        checks.return_value = [{"ticker": "000001", "checked_at": "2026-10-22T15:30:00", "close": 550, "decision": "SELL", "dynamic_stop_loss_pct": -6, "ma20": 520}]
        sales.return_value = [{"created_at": "2026-10-22T15:00:00", "name": "테스트", "sale_type": "partial", "quantity": 1, "price": 530, "cost_basis": 500, "realized_profit_loss": 30, "reason": "1차 익절"}]
        account = _kr_account("aggressive", "계좌")
        row = account["rows"][0]
        self.assertEqual(["테스트 (000001)", "2", "+91.67%"], row[:3])
        self.assertEqual(["₩500", "₩550", "2026-10-22 15:30", "₩+100", "+10.00%", "매도조건 충족", "₩470", "₩520"], row[4:])
        self.assertEqual("₩+30", account["realized"])
        self.assertEqual(["부분매도", "1", "₩530", "₩+30", "+6.00%", "1차 익절"], account["sales"][0][2:])

    @patch("stock_alarm.dashboard_v2.Path.exists", return_value=True)
    @patch("stock_alarm.dashboard_v2.recent_virtual_sales", return_value=[])
    @patch("stock_alarm.dashboard_v2.recent_price_quality")
    @patch("stock_alarm.dashboard_v2.recent_position_checks", return_value=[])
    @patch("stock_alarm.dashboard_v2.query_rows", return_value=[{"created_at": "2026-10-27T15:22:00", "cash": 0, "valuation": 1100, "equity": 1100, "profit_loss": 100}])
    @patch("stock_alarm.dashboard_v2.virtual_trader_state")
    def test_kr_account_uses_valid_saved_price_when_position_check_is_missing(self, state, _snapshots, _checks, quality, _sales, _exists):
        state.return_value = {"deposited": 1000, "realized_profit_loss": 0, "holdings": [{"name": "테스트", "ticker": "000001", "quantity": 2, "cost": 1000, "average_price": 500, "first_entry_at": "2026-10-27T09:00:00"}]}
        quality.return_value = [{"ticker": "000001", "created_at": "2026-10-27T15:22:00", "status": "valid", "close": 550}]
        account = _kr_account("neutral", "위험중립형")
        self.assertEqual("2026-10-27 15:22", account["priceAsOf"])
        self.assertEqual(["₩550", "2026-10-27 15:22", "₩+100", "+10.00%"], account["rows"][0][5:9])

    def test_comparison_chart_uses_common_dates_and_rebases_to_zero(self):
        chart = _comparison_chart([
            ("A", {"2026-10-19": 10, "2026-10-20": 21, "2026-10-21": 32}),
            ("B", {"2026-10-20": 5, "2026-10-21": 15.5, "2026-10-22": 20}),
        ])
        self.assertEqual(["2026-10-20", "2026-10-21"], chart["days"])
        self.assertEqual([0, 9.09], chart["series"][0]["values"])
        self.assertEqual([0, 10.0], chart["series"][1]["values"])

    @patch("stock_alarm.dashboard_v2.user_run_rows", return_value=[{"step": "positions_report", "status": "ok"}])
    @patch("stock_alarm.dashboard_v2.actionable_issue_rows", return_value=[{"source": "가격", "item": "000001", "status": "확인 필요"}])
    @patch("stock_alarm.dashboard_v2.latest_operational_status", return_value={
        "batch": {"label": "정상", "time": "2026-10-22T15:00:00"},
        "telegram": {"label": "확인 필요", "time": "기록 없음"},
        "price": {"label": "정상", "time": "2026-10-22T15:30:00"},
    })
    def test_kr_system_reuses_v1_operational_decisions(self, _status, _issues, _runs):
        system = _kr_system([{"name": "위험중립형", "priceIssues": ["테스트"]}])
        self.assertEqual(["알림 발송", "기록 없음", "기록 없음"], system["services"][1])
        self.assertEqual("확인 필요", system["services"][2][1])
        self.assertEqual(["가격", "000001", "확인 필요"], system["issues"][0])
        self.assertEqual(["보유종목 평가", "정상"], system["runs"][0])

    @patch("stock_alarm.dashboard_v2.user_run_rows", return_value=[])
    @patch("stock_alarm.dashboard_v2.actionable_issue_rows", return_value=[])
    @patch("stock_alarm.dashboard_v2.latest_operational_status", return_value={
        "batch": {"label": "정상", "time": "2026-10-20T15:00:00"},
        "telegram": {"label": "정상", "time": "2026-10-22T15:30:00"},
        "price": {"label": "오류", "time": "2026-10-20T15:30:00"},
    })
    def test_kr_system_marks_old_success_as_delayed(self, _status, _issues, _runs):
        system = _kr_system(market_day="2026-10-21")
        self.assertEqual(["갱신 지연", "정상", "오류"], [row[1] for row in system["services"]])
        self.assertEqual("갱신 지연", system["issues"][0][1])

    def test_us_system_marks_old_strategy_ledger_as_delayed(self):
        old = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        system = _us_system([{"date": old}], [SimpleNamespace(label="전략")],
                            [{"status": {"state": "ok", "updated_at": old}}], date.today().isoformat())
        self.assertEqual("갱신 지연", system["services"][1][1])
        self.assertEqual("갱신 지연", system["services"][2][1])
        self.assertTrue(any(row[0] == "전략" and row[1] == "갱신 지연" for row in system["issues"]))

    def test_us_experiment_risk_uses_saved_equity_and_waits_for_sharpe_sample(self):
        strategy = {"account": SimpleNamespace(label="미국 전략"), "status": {"state": "ok"},
                    "equity": [{"date": "20261019", "equity_usd": "100", "cash_usd": "50", "return_pct": "0"},
                               {"date": "20261020", "equity_usd": "120", "cash_usd": "40", "return_pct": "20"},
                               {"date": "20261021", "equity_usd": "90", "cash_usd": "30", "return_pct": "-10"}]}
        row = _us_experiments([strategy])[0]
        self.assertEqual("-25.00", row[5])
        self.assertEqual("기록 없음", row[6])
        self.assertEqual("정상", row[9])

    @patch("stock_alarm.dashboard_v2.ACCOUNTS", ())
    @patch("stock_alarm.dashboard_v2.read_csv")
    @patch("stock_alarm.dashboard_v2.recommendation_tracking_rows")
    def test_signal_history_keeps_prior_recommendations_and_sell_events(self, tracking, csv_rows):
        tracking.return_value = [{"pick_date": "2026-10-20", "name": "A", "score": "80",
                                  "tracking_status": "추적 중", "return_pct": "2", "entry_price": "1000",
                                  "current_price": "1020", "virtual_bought": "매수", "sell_alert_date": "2026-10-21",
                                  "sell_alert_price": "1030", "sell_alert_return_pct": "3", "sell_reason": "목표 도달"}]
        csv_rows.side_effect = lambda path: (
            [{"created_at": "2026-10-20T09:00:00", "name": "A", "ticker": "000001"}]
            if path == "logs/recommendations.csv" else
            [{"created_at": "2026-10-21T15:00:00", "name": "A", "ticker": "000001",
              "summary": "매도 조건", "return_pct": "3", "close": "1030", "sale_type": "full"}]
        )
        kr, us = _signal_history()
        self.assertEqual(["매도 신호", "매수 추천"], [row["signal"] for row in kr])
        self.assertEqual("000001", kr[1]["ticker"])
        self.assertEqual("₩1,000", kr[1]["detail"]["entryPrice"])
        self.assertEqual("₩1,030", kr[1]["detail"]["sellPrice"])
        self.assertEqual("목표 도달", kr[1]["detail"]["sellReason"])
        self.assertEqual("2026-10-21", kr[0]["detail"]["sellDate"])
        self.assertEqual("₩1,030", kr[0]["detail"]["sellPrice"])
        self.assertEqual("전량 매도 검토", kr[0]["status"])
        self.assertEqual([], us)

    @patch("stock_alarm.dashboard_v2._signal_history", return_value=[])
    def test_initial_view_does_not_build_domestic_signal_history(self, history):
        dashboard_v2_payload()
        self.assertEqual("us", history.call_args.args[0])
        self.assertEqual(1, history.call_count)

    @patch("stock_alarm.dashboard_v2.ACCOUNTS", (SimpleNamespace(label="미국", path=lambda name: name),))
    @patch("stock_alarm.dashboard_v2.read_strategy")
    @patch("stock_alarm.dashboard_v2.read_ledger", return_value=[])
    def test_us_compare_reuses_strategy_read(self, _ledger, strategy):
        strategy.return_value = {"equity": [{"date": "20261020", "equity_usd": "100", "cash_usd": "50", "return_pct": "0"},
                                            {"date": "20261021", "equity_usd": "110", "cash_usd": "50", "return_pct": "10"}],
                                 "status": {"state": "ok"}, "account": SimpleNamespace(label="미국")}
        result = dashboard_v2_payload("us", "compare")
        self.assertIn("compareChart", result)
        self.assertEqual(1, strategy.call_count)

    @patch("stock_alarm.dashboard_v2._cached_signal_history")
    def test_domestic_all_shows_one_recommendation_row_with_linked_sell(self, history):
        history.return_value = [
            {"date": "2026-10-21", "name": "A", "ticker": "1", "signal": "매도 신호", "reason": "매도", "status": "알림"},
            {"date": "2026-10-20", "name": "A", "ticker": "1", "signal": "매수 추천", "reason": "추천", "status": "매도 알림", "detail": {"sellDate": "2026-10-21"}},
            {"date": "2026-10-19", "name": "A", "ticker": "1", "signal": "매수 추천", "reason": "추천", "status": "추적 중"},
        ]
        result = dashboard_v2_payload("kr", "signals")
        self.assertEqual(2, result["signalHistoryTotal"])
        self.assertEqual("2026-10-21", result["signalHistory"][0]["detail"]["sellDate"])
        self.assertEqual(1, dashboard_v2_payload("kr", "signals", filter_name="매도 신호")["signalHistoryTotal"])
        self.assertEqual(1, dashboard_v2_payload("kr", "signals", filter_name="추적 중")["signalHistoryTotal"])
        self.assertEqual(3, dashboard_v2_payload("us", "signals")["signalHistoryTotal"])

    @patch("stock_alarm.dashboard_v2._cached_signal_history")
    def test_signal_history_is_filtered_and_paged_on_server(self, history):
        history.return_value = [{"name": f"종목{i}", "ticker": str(i), "reason": "근거", "signal": "매수 추천", "status": "추적 중"} for i in range(25)]
        result = dashboard_v2_payload("kr", "signals", page=1, filter_name="매수 추천", query="종목")
        self.assertEqual(25, result["signalHistoryTotal"])
        self.assertEqual(5, len(result["signalHistory"]))

    @patch("stock_alarm.dashboard_v2._signal_history")
    @patch("stock_alarm.dashboard_v2._history_source_signature")
    def test_signal_cache_rebuilds_when_source_changes(self, signature, history):
        _cached_signal_history.cache_clear()
        signature.return_value = (("source", 1, 1),)
        history.return_value = [{"name": "처음", "ticker": "1", "reason": "근거", "signal": "매수 추천", "status": "추적 중"}]
        self.assertEqual("처음", dashboard_v2_payload("kr", "signals")["signalHistory"][0]["name"])
        history.return_value = [{"name": "변경", "ticker": "1", "reason": "근거", "signal": "매수 추천", "status": "추적 중"}]
        self.assertEqual("처음", dashboard_v2_payload("kr", "signals")["signalHistory"][0]["name"])
        signature.return_value = (("source", 2, 2),)
        self.assertEqual("변경", dashboard_v2_payload("kr", "signals")["signalHistory"][0]["name"])
        self.assertEqual(2, history.call_count)
        _cached_signal_history.cache_clear()

    def test_signal_source_signature_changes_with_us_files(self):
        with tempfile.TemporaryDirectory() as directory:
            account = SimpleNamespace(path=lambda name: str(Path(directory) / name))
            with patch("stock_alarm.dashboard_v2.ACCOUNTS", (account,)):
                before = _history_source_signature("us")
                (Path(directory) / "decisions.csv").write_text("row", encoding="utf-8")
                after = _history_source_signature("us")
                self.assertNotEqual(before, after)


class DashboardV2ServerTest(unittest.TestCase):
    def test_read_only_routes_and_same_origin(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardV2Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with patch("stock_alarm.dashboard_v2_server.dashboard_v2_payload", return_value={"kr": {"signals": []}, "us": {"signals": []}}):
                request = urllib.request.Request(url + "/api/v2", headers={"Origin": url})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual([], json.load(response)["kr"]["signals"])
            with patch("stock_alarm.dashboard_v2_server.real_account_payload", return_value={"connected": False}):
                with urllib.request.urlopen(url + "/api/v2/real-account") as response:
                    self.assertFalse(json.load(response)["connected"])
            for origin in ("null", "https://other.example"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url + "/api/v2/real-account", headers={"Origin": origin}))
                self.assertEqual(403, error.exception.code)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url + "/api/v2/real-account", headers={"Sec-Fetch-Site": "cross-site"}))
            self.assertEqual(403, error.exception.code)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url + "/api/v2/real-account", headers={"Host": "other.example"}))
            self.assertEqual(403, error.exception.code)
            with urllib.request.urlopen(url + "/v2") as response:
                page = response.read()
                self.assertIn(b"dashboard-v2-shell.js", page)
                self.assertIn(b"dashboard-v2-live.js", page)
                self.assertIn(b"dashboard-v2.css", page)
                self.assertNotIn(b"<script>", page)
                self.assertNotIn(b"<style>", page)
                self.assertIn("script-src 'self';", response.headers["Content-Security-Policy"])
                self.assertNotIn("script-src 'self' 'unsafe-inline'", response.headers["Content-Security-Policy"])
                self.assertIn("style-src 'self';", response.headers["Content-Security-Policy"])
                self.assertNotIn("'unsafe-inline'", response.headers["Content-Security-Policy"])
            with urllib.request.urlopen(url + "/dashboard-v2.css") as response:
                self.assertIn(b".chart-frame", response.read())
            with urllib.request.urlopen(url + "/dashboard-v2-shell.js") as response:
                self.assertIn(b"function render()", response.read())
            with urllib.request.urlopen(url + "/dashboard-v2-live.js") as response:
                self.assertIn(b"fetch('/api/v2'", response.read())
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url + "/api/v2", data=b"{}"))
            self.assertEqual(405, error.exception.code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_initial_payload_is_small_and_views_include_details(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardV2Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        record = {"day": "2026-10-22", "updated": "22:00 KST", "market": [], "signals": [],
                  "bench": "KOSPI", "chart": [], "accounts": [{"name": "계좌", "rows": [["종목"]], "sales": []}],
                  "signalHistory": [{"name": "신호"}] * 10, "compareChart": {"days": []},
                  "experiments": [], "research": [], "system": {"services": []}}
        try:
            today = {key: value for key, value in record.items() if key not in {"signalHistory", "compareChart", "experiments", "research", "system"}}
            today["accounts"] = [{key: value for key, value in account.items() if key not in {"rows", "sales"}} for account in record["accounts"]]
            today["signalHistoryCount"] = 10
            today["recentDecisions"] = record["signalHistory"][:7]
            def payload(market=None, view="today", **kwargs):
                if market is None:
                    return {"kr": today, "us": today}
                return {"signals": {"signalHistory": record["signalHistory"]},
                        "account": {"accounts": record["accounts"]}}[view]
            with patch("stock_alarm.dashboard_v2_server.dashboard_v2_payload", side_effect=payload) as build:
                with urllib.request.urlopen(url + "/api/v2") as response:
                    initial = json.load(response)["kr"]
                self.assertEqual(10, initial["signalHistoryCount"])
                self.assertEqual(7, len(initial["recentDecisions"]))
                self.assertNotIn("rows", initial["accounts"][0])
                self.assertNotIn("signalHistory", initial)
                with urllib.request.urlopen(url + "/api/v2/view?market=kr&view=signals") as response:
                    self.assertEqual(10, len(json.load(response)["signalHistory"]))
                with urllib.request.urlopen(url + "/api/v2/view?market=kr&view=account") as response:
                    self.assertEqual([["종목"]], json.load(response)["accounts"][0]["rows"])
                self.assertEqual([(), ("kr", "signals"), ("kr", "account")], [call.args for call in build.call_args_list])
                self.assertEqual({"page": 0, "filter_name": "전체", "query": ""}, build.call_args_list[1].kwargs)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + "/api/v2/view?market=bad&view=account")
                self.assertEqual(400, error.exception.code)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + "/api/v2/view?market=kr&view=signals&page=-1")
                self.assertEqual(400, error.exception.code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
