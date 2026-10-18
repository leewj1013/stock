"""Optional Korean display names for frozen US universes.

The strategy engine reads ``name_eng`` and ``code`` only. This module stays
outside its frozen code hash so display-name updates do not halt trading.
"""

import csv
import os
from pathlib import Path

from .us_strategy_account import ACCOUNTS


def enrich_universe_names(accounts=ACCOUNTS) -> int:
    rows_by_account = []
    for account in accounts:
        path = account.path("universe.csv")
        if path.exists():
            with path.open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            if rows and any(not row.get("name_ko") for row in rows):
                rows_by_account.append((path, rows))
    if not rows_by_account:
        return 0

    from .us_history_collect import top_by_market_cap

    required = max(len(rows) for _, rows in rows_by_account)
    names = {stock["reutersCode"]: stock.get("stockName", "") for stock in top_by_market_cap(required + 50)}
    changed = 0
    for path, rows in rows_by_account:
        updates = 0
        for row in rows:
            if not row.get("name_ko") and names.get(row["code"]):
                row["name_ko"] = names[row["code"]]
                updates += 1
        if not updates:
            continue
        fields = ["code", "name_ko", *[key for key in rows[0] if key not in ("code", "name_ko")]]
        temporary = Path(f"{path}.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
        changed += updates
    return changed
