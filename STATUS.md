# stockAlarm status

## Current MVP

- Data source: Naver Finance daily prices
- Watchlist: `data/watchlist.csv` with 102 tickers
- Notifier: Telegram
- Schedule: Windows tasks at 08:55, 10:30, 13:30, 15:00, 16:10
- Duplicate guard: same message is skipped once per day unless `FORCE_SEND=1`
- Recommendation alert: top 5 candidates from the watchlist
- Sell-review alerts: `python -m stock_alarm.sell_check` checks `data/positions.csv`
- Empty sell-review alerts: off by default; daily summary still says `매도 검토: 없음`
- Auto tracking: new recommendations are appended to `data/positions.csv` when `AUTO_TRACK_PICKS=1`
- Position validation: `python -m stock_alarm.positions_check`
- Position P/L report: `python -m stock_alarm.positions_report`
- Recommendation performance: `python -m stock_alarm.recommendation_performance`
- Daily summary: `python -m stock_alarm.daily_summary`
- Dashboard: `python -m stock_alarm.dashboard` writes `reports/dashboard.html`
- Dashboard macro: double-click `open_dashboard.bat`
- Dashboard issues alert: double-click `issue_alert.bat` or run `python -m stock_alarm.issue_alert`
- Failure alert: `python -m stock_alarm.failure_alert <step> <exit_code>`
- Scheduled run: `scripts/run_stock_alarm.ps1` supports `open`, `intraday`, `performance`, and default `daily` modes
- Reboot startup macro: double-click `start_stock_alarm.bat`
- Position P/L history: `logs/positions_report.csv`, including change since the previous snapshot
- OpenDART hook: enabled when `DART_LOOKUP=1` and `DART_API_KEY` exists

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

Last verified local operation check:

```text
2026-07-25
start_stock_alarm.bat flow: ok
scheduled tasks: stockAlarmOpen, stockAlarmIntraday1030, stockAlarmIntraday1330, stockAlarmIntraday1500, stockAlarmDaily ready
telegram: ready
dart: ready, lookup on, score weight 1
task_error: none
dashboard_ready: ok
dashboard: reports/dashboard.html generated
next_run: shown by scripts/status_daily_task.ps1 as next_run=<task> at <time>
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
SELL_PROTECT_PROFIT_PCT=5
SELL_GIVEBACK_PCT=4
SEND_EMPTY_SELL_ALERT=0
SEND_DAILY_CHECK_ALERT=0
NEWS_LOOKUP=0
NEWS_SCORE_WEIGHT=0
MIN_RECOMMEND_SCORE=0
DART_API_KEY=
DART_LOOKUP=0
DART_SCORE_WEIGHT=1
```

`MIN_MARKET_UP_RATIO` exists for experiments but is off by default because the 45% market filter reduced backtest quality.

## Dashboard shape

Keep one combined dashboard at `reports/dashboard.html`.

- Top: issues, today run details, recommendation reasons, sell alert summary, recent sell alerts
- Recommendation shape: watch candidate, review needed, sell review
- Middle: recommendation statistics, best/worst recommendations, sell-alert linked picks
- Bottom: latest recommendations, positions, raw logs

## Next best step

Watch `scripts/status_daily_task.ps1` for `next_run=...`, then check `python -m stock_alarm.daily_check` and `reports/dashboard.html`.
