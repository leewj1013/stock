# stockAlarm

Korean stock watchlist alert MVP. Telegram messages are formatted in Korean.

Current rule:

- Today's volume is at least 1.5x the previous 20 trading-day average
- Today's close is above the 20-day moving average
- Trading value is at least 5,000,000,000 KRW
- Today's absolute price move is at most 8%
- Recent average intraday range is at most 12%
- Send top 5 picks

Score is capped at 100:

- Volume spike: 45 points
- Trading value: 35 points
- Distance above MA20: 20 points

This is a rule-based watchlist alert, not investment advice.

Recent 60-trading-day tuning kept the default volume rule at `VOLUME_MULTIPLIER=1.5`. The edge is small, so treat alerts as candidates to review, not buy/sell signals.

## Daily operation

Normal daily flow:

```powershell
.\.venv\Scripts\python -m stock_alarm.health
.\.venv\Scripts\python -m stock_alarm.report
.\.venv\Scripts\python -m stock_alarm.daily_check
.\.venv\Scripts\python -m stock_alarm.sell_check
.\.venv\Scripts\python -m stock_alarm.positions_check
.\.venv\Scripts\python -m stock_alarm.positions_report
.\.venv\Scripts\python -m stock_alarm.daily_summary
```

`health` masks secrets and shows operational toggles such as Telegram readiness, auto-tracking, empty sell alerts, and sell thresholds.

Before committing, check that no obvious secret was copied into public files:

```powershell
.\.venv\Scripts\python -m stock_alarm.preflight
```

Manual alert preview without Telegram:

```powershell
$env:NOTIFIER='console'
$env:FORCE_SEND='1'
.\.venv\Scripts\python -m stock_alarm
```

Manual Telegram resend:

```powershell
$env:FORCE_SEND='1'
.\.venv\Scripts\python -m stock_alarm
```

Sell-review alert for positions in `data/positions.csv`:

```powershell
.\.venv\Scripts\python -m stock_alarm.sell_check
```

Sell-review also alerts when a position return worsens by `SELL_DROP_PCT` points or more since the latest position snapshot.

By default, no separate sell-review message is sent when there are no sell-review candidates. Set `SEND_EMPTY_SELL_ALERT=1` if you want the old "none" message too.

Sell-review alerts are logged to:

```text
logs/sell_alerts.csv
```

Daily Telegram summary:

```powershell
.\.venv\Scripts\python -m stock_alarm.daily_summary
```

The summary includes recommendation count, sell-review count, position P/L, and recommendation TOP3.

If a scheduled step fails, `scripts/run_stock_alarm.ps1` sends a failure alert with the failed step name and exit code.

Position P/L reports are logged to:

```text
logs/positions_report.csv
```

After at least two snapshots, the report also shows average P/L change since the previous snapshot.

If the scheduled task status is unavailable in Python, use:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\status_daily_task.ps1
```

## Install

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Configure

```powershell
Copy-Item .env.example .env
Copy-Item data\positions.example.csv data\positions.csv
```

Set Kakao values in `.env` when Kakao Login setup is ready.

```text
KAKAO_REST_API_KEY=...
KAKAO_REDIRECT_URI=http://127.0.0.1:8080/oauth
KAKAO_ACCESS_TOKEN=
KAKAO_REFRESH_TOKEN=
NOTIFIER=telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DATA_SOURCE=naver
KOREAN_STOCK_NAMES=1
AUTO_TRACK_PICKS=1
STOCKS=
```

If notifier credentials are empty, the app prints the message to the console.

## Telegram setup

Needed values:

```text
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Fast setup:

1. Open Telegram and talk to `@BotFather`.
2. Send `/newbot`, choose a name, and copy the bot token.
3. Send any message to your new bot.
4. Open this URL in a browser, replacing the token:

```text
https://api.telegram.org/botYOUR_TOKEN/getUpdates
```

5. Copy `message.chat.id` into `TELEGRAM_CHAT_ID`.

Then set:

```text
NOTIFIER=telegram
TELEGRAM_BOT_TOKEN=YOUR_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

Send a small Telegram test message:

```powershell
.\.venv\Scripts\python -m stock_alarm.telegram_test
```

Check the bot token without sending a message:

```powershell
.\.venv\Scripts\python -m stock_alarm.telegram_check
```

Deliveries are logged to:

```text
logs/deliveries.csv
```

Duplicate messages are skipped once per day. To force a resend:

```powershell
$env:FORCE_SEND='1'
.\.venv\Scripts\python -m stock_alarm
```

## Kakao token helper

Kakao is optional now. Use it only if `NOTIFIER=kakao`.

```powershell
.\.venv\Scripts\python -m stock_alarm.kakao_auth
```

Open the printed URL, approve Kakao Talk message permission, and the helper saves tokens to `.env`.

If the local redirect server cannot be used, print the URL and manually exchange the `code` value copied from the redirect URL:

```powershell
.\.venv\Scripts\python -m stock_alarm.kakao_auth --print-url
.\.venv\Scripts\python -m stock_alarm.kakao_auth --code YOUR_AUTH_CODE
```

## Run

```powershell
.\.venv\Scripts\python -m stock_alarm
```

Recommendations are appended to `logs/recommendations.csv`.

When `AUTO_TRACK_PICKS=1`, new recommendations are also appended to `data/positions.csv` for future sell-review checks. Existing tickers are not overwritten.

Default Naver mode reads the starter watchlist from `data/watchlist.csv`. Set `STOCKS` only when you want to override it:

```text
STOCKS=005930:Samsung Electronics,035720:Kakao
```

Check the starter watchlist:

```powershell
.\.venv\Scripts\python -m stock_alarm.watchlist_check
```

## Backtest

Run the same rule against recent trading days and write returns to `logs/backtest.csv`.

```powershell
.\.venv\Scripts\python -m stock_alarm.backtest
```

Optional `.env` values:

```text
AS_OF_DATE=2024-07-24
BACKTEST_DAYS=20
BACKTEST_HOLD_DAYS=1
```

`DATA_SOURCE=naver` is the default fallback. It checks tickers in `data/watchlist.csv`, or `STOCKS` when provided.

Backtest summary is written to `logs/backtest_summary.csv`.

Naver responses are cached in `.cache/naver/`. To bypass cache once:

```powershell
$env:NO_CACHE='1'
.\.venv\Scripts\python -m stock_alarm.tune
```

## Tune rules

Compare a few volume and holding-day combinations:

```powershell
.\.venv\Scripts\python -m stock_alarm.tune
```

Output:

```text
logs/tuning.csv
```

Optional `.env` values:

```text
TUNE_VOLUME_MULTIPLIERS=1.5,2.0,2.5
TUNE_HOLD_DAYS=1,3,5
```

Recommend settings from `logs/tuning.csv`:

```powershell
.\.venv\Scripts\python -m stock_alarm.tune_report
```

## Daily Windows task

Register a daily 16:10 task:

```powershell
.\scripts\register_daily_task.ps1
```

Check task status:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\status_daily_task.ps1
```

Manual script run:

```powershell
.\scripts\run_stock_alarm.ps1
```

Scheduled runs append console output to:

```text
logs/task.out.log
logs/task.err.log
```

The scheduled script runs both:

```text
python -m stock_alarm
python -m stock_alarm.sell_check
python -m stock_alarm.positions_report
python -m stock_alarm.daily_summary
```

After 16:10, check today's delivery result:

```powershell
.\.venv\Scripts\python -m stock_alarm.daily_check
```

This reports today's delivery status and whether any logged error happened after the latest delivery.

## Healthcheck

Check local configuration without printing secret values:

```powershell
.\.venv\Scripts\python -m stock_alarm.health
```

## Report

Show recent deliveries, recommendations, task status, and latest error:

```powershell
.\.venv\Scripts\python -m stock_alarm.report
```

## Cleanup logs

Preview log cleanup:

```powershell
.\.venv\Scripts\python -m stock_alarm.cleanup_logs
```

Archive logs to `logs/archive/` and truncate current log files:

```powershell
.\.venv\Scripts\python -m stock_alarm.cleanup_logs --apply
```
