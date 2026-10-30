import csv
from unittest.mock import patch
from types import SimpleNamespace
import pytest

from stock_alarm import us_spy_hold


def test_update_rebuilds_ledger_with_whole_shares(tmp_path):
    path = str(tmp_path / "hold.csv")
    with patch.object(us_spy_hold, "spy_closes", return_value=[("20261005", 774.83), ("20261006", 790.0)]):
        assert us_spy_hold.update(path) == 2
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert rows[0]["shares"] == "129" and rows[0]["equity_usd"] == "100000.0"
    assert rows[1]["equity_usd"] == str(round(129 * 790.0 + 46.93, 2))


@pytest.mark.parametrize('failure',['write','replace'])
def test_failed_spy_publication_preserves_previous_ledger(tmp_path,failure):
    path=tmp_path/'spy.csv';path.write_text('confirmed')
    target='stock_alarm.us_spy_hold.csv.writer' if failure=='write' else 'stock_alarm.us_spy_hold.os.replace'
    error=OSError('disk failure')
    with patch.object(us_spy_hold,'spy_closes',return_value=[(us_spy_hold.START_DATE,100.0)]):
        if failure=='write':
            replacement=SimpleNamespace(writerow=lambda _: (_ for _ in ()).throw(error))
            with patch(target,return_value=replacement),pytest.raises(OSError):us_spy_hold.update(str(path))
        else:
            with patch(target,side_effect=error),pytest.raises(OSError):us_spy_hold.update(str(path))
    assert path.read_text()=='confirmed'
    assert len(list(tmp_path.iterdir()))==1


def test_invalid_spy_price_preserves_previous_ledger(tmp_path):
    path=tmp_path/'spy.csv';path.write_text('confirmed')
    with patch.object(us_spy_hold,'spy_closes',return_value=[(us_spy_hold.START_DATE,100.0),('20261006',float('nan'))]),pytest.raises(ValueError):
        us_spy_hold.update(str(path))
    assert path.read_text()=='confirmed'
