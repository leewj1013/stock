from __future__ import annotations

from .data_store import DB_PATH

# Sizing and exit thresholds differ per profile, and (since the profile
# scoring feature) so does which candidates get picked in the first place:
# scoring_weights re-ranks the shared, already quality-filtered candidate
# pool (see app.py:select_for_profile) -- it never loosens the existing
# safety filters. None means "use the existing env-driven default", so the
# aggressive profile's behavior is unchanged from before profiles existed.
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
        # 성장성/모멘텀 중심 -- 안정성/배당 비중은 낮춘다.
        "scoring_weights": {"profitability": 0.20, "growth": 0.30, "stability": 0.10, "dividend": 0.05, "momentum": 0.25, "news": 0.10},
        "max_volatility_atr_pct": None,
        "max_holdings": 7,
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
        # 안정성/수익성 중심 -- 모멘텀 비중은 낮춘다.
        "scoring_weights": {"profitability": 0.25, "growth": 0.20, "stability": 0.30, "dividend": 0.10, "momentum": 0.10, "news": 0.05},
        "max_volatility_atr_pct": 6.0,
        "max_holdings": 10,
    },
}

CATEGORY_VALUE_KEYS = {
    "profitability": "profitability_score",
    "growth": "growth_score",
    "stability": "stability_score",
    "dividend": "dividend_score",
    "momentum": "momentum_score",
    "news": "news_category_score",
}
