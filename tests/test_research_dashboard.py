import os
import tempfile
import unittest

from stock_alarm.dashboard import decision_log_rows, risk_adjusted

NOTES = """# 전략 운영 메모

## 결정 기록

### 2026-09-29 · 청산 규칙 유지
- 20일선 이탈 매도 후 반등은 월별로 부호가 바뀝니다.
- 결정: 청산 규칙 조정은 여기서 멈춥니다.

### 2026-09-18 · 추천 알림은 가상매수 체결 시에만
- 근거: 추천 알림이 하루 16.5건이었습니다.
- 변경: 체결된 추천만 즉시 발송합니다.

### 2026-09-10 · 임계값 조정
- 근거만 있는 항목

## 진행 중인 실험
### 2026-01-01 · 이건 결정 기록이 아님
"""


class DecisionLogTest(unittest.TestCase):
    def test_reads_only_the_decision_section_and_prefers_the_decision_bullet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "notes.md")
            with open(path, "w", encoding="utf-8") as file:
                file.write(NOTES)
            rows = decision_log_rows(path)

        self.assertEqual(["2026-09-29", "2026-09-18", "2026-09-10"], [row["decision_date"] for row in rows])
        self.assertEqual("청산 규칙 유지", rows[0]["decision_title"])
        self.assertEqual("청산 규칙 조정은 여기서 멈춥니다.", rows[0]["decision_summary"])
        self.assertEqual("체결된 추천만 즉시 발송합니다.", rows[1]["decision_summary"])
        self.assertEqual("근거만 있는 항목", rows[2]["decision_summary"])

    def test_missing_file_is_empty(self):
        self.assertEqual([], decision_log_rows("does-not-exist.md"))


class ResearchResultsTest(unittest.TestCase):
    def test_table_summary_and_full_reports(self):
        from stock_alarm.dashboard import research_results_section

        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "predictions.csv"), "w", encoding="utf-8") as file:
                file.write("date,slug,predicted_verdict,predicted_probability,actual_verdict,hit\n"
                           "2026-09-30,low_vol,기각,0.60,근거 있음,0\n2026-10-03,holding,기각,0.55,기각,1\n")
            with open(os.path.join(directory, "2026-09-30-low_vol.md"), "w", encoding="utf-8") as file:
                file.write("# 저변동성 보고서 본문")
            html = research_results_section(directory)

        self.assertIn("사전 예측 2건 중 1건 적중", html)
        self.assertIn("평균 예측 확신도 57%", html)  # (60+55)/2 = 57.5, formatted as 57
        self.assertIn("빗나감", html)
        self.assertIn("저변동성 보고서 본문", html)

    def test_no_predictions_shows_empty_state(self):
        from stock_alarm.dashboard import research_results_section

        with tempfile.TemporaryDirectory() as directory:
            self.assertIn("아직 사전 등록 연구가 없습니다", research_results_section(directory))


class RiskAdjustedTest(unittest.TestCase):
    def test_bear_return_uses_the_previous_sessions_regime(self):
        regimes = {"2026-09-15": "bull", "2026-09-16": "bear", "2026-09-17": "bear"}
        daily = [("2026-09-16", 110), ("2026-09-17", 99)]  # +10% after a bull session, -10% after a bear one
        sharpe, bear = risk_adjusted(100, daily, regimes)
        self.assertEqual("", sharpe)  # fewer than 20 returns
        self.assertEqual("-10.00", bear)

    def test_sharpe_only_after_twenty_returns(self):
        equities = [100 + i + (i % 3) for i in range(1, 21)]
        daily = [(f"2026-10-{day:02d}", equity) for day, equity in zip(range(1, 21), equities)]
        self.assertEqual("", risk_adjusted(100, daily[:19], {})[0])
        sharpe, _bear = risk_adjusted(100, daily, {})
        self.assertNotEqual("", sharpe)
        self.assertGreater(float(sharpe), 0)


if __name__ == "__main__":
    unittest.main()
