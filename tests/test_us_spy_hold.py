import csv
from unittest.mock import patch

from stock_alarm import us_spy_hold


def test_update_rebuilds_ledger_with_whole_shares(tmp_path):
    path = str(tmp_path / "hold.csv")
    with patch.object(us_spy_hold, "spy_closes", return_value=[("20261005", 774.83), ("20261006", 790.0)]):
        assert us_spy_hold.update(path) == 2
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert rows[0]["shares"] == "129" and rows[0]["equity_usd"] == "100000.0"
    assert rows[1]["equity_usd"] == str(round(129 * 790.0 + 46.93, 2))
