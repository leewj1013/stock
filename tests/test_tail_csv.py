import csv

from stock_alarm.report import tail_csv


def test_tail_matches_full_parse(tmp_path):
    path = tmp_path / "log.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["ticker", "name"])
        for index in range(30000):  # > 1 MiB, so the tail spans read blocks
            writer.writerow([f"{index:06d}", f"종목, {index}" * 5])
    with open(path, newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    for count in (1, 7, 1000, 29999, 30000, 50000):
        assert tail_csv(str(path), count) == rows[-count:]
    assert tail_csv(str(tmp_path / "missing.csv")) == []
