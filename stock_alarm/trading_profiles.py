from __future__ import annotations

from .data_store import DB_PATH

# Both profiles buy the same recommended tickers (same recommend() picks) --
# only sizing and exit thresholds differ. None means "use the existing
# env-driven default", so the aggressive profile's behavior is unchanged.
PROFILES = {
    "aggressive": {
        "db_path": DB_PATH,
        "sell_alerts_log": "logs/sell_alerts.csv",
        "notify": True,
        "sector_cap_pct": None,
        "exposure_limit_pct": None,
        "min_position_pct": None,
        "max_position_pct": None,
        "sell_policy": None,
    },
    "neutral": {
        "db_path": "data/stock_alarm_neutral.db",
        "sell_alerts_log": "logs/sell_alerts_neutral.csv",
        "notify": False,
        "sector_cap_pct": 30.0,
        "exposure_limit_pct": 100.0,
        "min_position_pct": 10.0,
        "max_position_pct": 20.0,
        "sell_policy": {"stop_loss_pct": 3.0, "take_profit_1_pct": 7.0, "take_profit_2_pct": 14.0},
    },
}
