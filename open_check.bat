@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m stock_alarm.open_check
) else (
  python -m stock_alarm.open_check
)
pause
