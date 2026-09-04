import email.utils
import json
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from stock_alarm.point_in_time_collect import PartialCollectionError, collect_news
from stock_alarm.point_in_time_store import connect


class PointInTimeCollectTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
