import json
import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.dart_reference import keyword_score, load_corp_codes, reference


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


if __name__ == "__main__":
    unittest.main()
