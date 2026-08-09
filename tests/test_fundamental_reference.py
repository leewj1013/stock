import os
import unittest
from unittest.mock import MagicMock, patch

from stock_alarm.fundamental_reference import naver_snapshot, snapshot


class FundamentalReferenceTest(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_disabled_by_default(self):
        self.assertEqual("disabled", snapshot("005930")["financial_notes"])

    @patch("stock_alarm.fundamental_reference.urllib.request.urlopen")
    def test_naver_scores_positive_reasonable_fundamentals(self, urlopen):
        response = MagicMock()
        response.read.return_value = b'<em id="_per">12.00</em><em id="_pbr">1.50</em><em id="_dvr">2.00</em>'
        urlopen.return_value.__enter__.return_value = response
        self.assertEqual(4, naver_snapshot("005930")["financial_score"])


if __name__ == "__main__":
    unittest.main()
