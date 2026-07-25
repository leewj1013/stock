import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.dart_reference import keyword_score, lines, load_corp_codes, reference


class DartReferenceTest(unittest.TestCase):
    def test_keyword_score(self):
        score, notes = keyword_score(["영업이익 증가", "적자 감소"])

        self.assertEqual(0, score)
        self.assertIn("good=2", notes)
        self.assertIn("bad=2", notes)

    def test_load_corp_codes(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as file:
            json.dump({"005930": "00126380"}, file)
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))

        self.assertEqual("00126380", load_corp_codes(file.name)["005930"])

    @patch("stock_alarm.dart_reference.disclosure_titles", return_value=["매출 증가"])
    def test_reference(self, _titles):
        self.assertEqual(("2", "dart=1 good=2 bad=0"), reference("005930"))


    @patch.dict(os.environ, {}, clear=True)
    @patch("stock_alarm.dart_reference.corp_code_by_stock", return_value="")
    def test_lines_explains_missing_key_or_corp_code(self, _corp):
        text = "\n".join(lines("005930"))

        self.assertIn("DART_API_KEY=missing", text)
        self.assertIn("corp_code=missing", text)

    @patch.dict(os.environ, {"DART_API_KEY": "x"})
    @patch("stock_alarm.dart_reference.disclosure_titles", return_value=["매출 증가"])
    @patch("stock_alarm.dart_reference.corp_code_by_stock", return_value="00126380")
    def test_lines_includes_score(self, _corp, _titles):
        text = "\n".join(lines("005930"))

        self.assertIn("DART_API_KEY=ok", text)
        self.assertIn("score=2", text)


if __name__ == "__main__":
    unittest.main()
