"""Forward record of a buy-and-hold SPY account (control for a future US strategy account).

Derived entirely from Naver daily closes, so every run rebuilds the ledger and a missed day heals itself.
Price return only (no dividends); whole shares, remainder kept as cash; bought at the START_DATE close.
"""
import csv
import json
import os
import urllib.request

LEDGER_PATH = "data/us_spy_hold.csv"
START_DATE = "20261005"
CAPITAL_USD = 100_000


def spy_closes(start: str = START_DATE) -> list[tuple[str, float]]:
    url = f"https://api.stock.naver.com/chart/foreign/item/SPY/day?startDateTime={start}0000&endDateTime=209912310000"
    request = urllib.request.Request(url, headers={"User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return [(row["localDate"], float(row["closePrice"])) for row in json.load(response)]


def update(path: str = LEDGER_PATH) -> int:
    closes = spy_closes()
    if not closes or closes[0][0] != START_DATE:
        raise RuntimeError(f"SPY series does not start at {START_DATE}: {closes[:1]}")
    shares = int(CAPITAL_USD // closes[0][1])
    cash = round(CAPITAL_USD - shares * closes[0][1], 2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "spy_close", "shares", "cash_usd", "equity_usd", "return_pct"])
        for day, close in closes:
            equity = round(shares * close + cash, 2)
            writer.writerow([day, close, shares, cash, equity, round((equity / CAPITAL_USD - 1) * 100, 4)])
    return len(closes)


if __name__ == "__main__":
    print(update())
