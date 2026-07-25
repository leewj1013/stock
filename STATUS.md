# stockAlarm status

## Current MVP

- Data source: Naver Finance daily prices
- Watchlist: `data/watchlist.csv` with 102 tickers
- Notifier: Telegram
- Schedule: Windows task `stockAlarmDaily` at 16:10
- Duplicate guard: same message is skipped once per day unless `FORCE_SEND=1`
- Recommendation alert: top 5 candidates from the watchlist
- Sell-review alerts: `python -m stock_alarm.sell_check` checks `data/positions.csv`
- Empty sell-review alerts: off by default; daily summary still says `매도 검토: 없음`
- Auto tracking: new recommendations are appended to `data/positions.csv` when `AUTO_TRACK_PICKS=1`
- Position validation: `python -m stock_alarm.positions_check`
- Position P/L report: `python -m stock_alarm.positions_report`
- Daily summary: `python -m stock_alarm.daily_summary`
- Failure alert: `python -m stock_alarm.failure_alert <step> <exit_code>`
- Scheduled run: `scripts/run_stock_alarm.ps1` runs recommendation, sell-review, position report, daily summary, and failure alert on step errors
- Position P/L history: `logs/positions_report.csv`, including change since the previous snapshot

## Verified

```powershell
.\.venv\Scripts\python -m stock_alarm.preflight
.\.venv\Scripts\python -m stock_alarm.health
.\.venv\Scripts\python -m stock_alarm.daily_check
$env:NOTIFIER='console'; $env:FORCE_SEND='1'; powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_stock_alarm.ps1
```

Last verified Telegram delivery:

```text
2026-07-25T02:23:17,telegram
```

Last verified full-chain dry run:

```text
2026-07-25T02:19:43,console
```

## Defaults

```text
DATA_SOURCE=naver
NOTIFIER=telegram
TOP_N=5
AUTO_TRACK_PICKS=1
VOLUME_MULTIPLIER=1.5
BACKTEST_HOLD_DAYS=1
MIN_MARKET_UP_RATIO=0
SELL_LOSS_PCT=5
SELL_DROP_PCT=3
SEND_EMPTY_SELL_ALERT=0
```

`MIN_MARKET_UP_RATIO` exists for experiments but is off by default because the 45% market filter reduced backtest quality.

## Next best step

If you explicitly approve sending position/sell-review summary data to Telegram, run one full Telegram-mode chain; otherwise commit the MVP and check `daily_check` after the scheduled 16:10 run.
