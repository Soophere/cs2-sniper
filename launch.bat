@echo off
title CS2 Sniper
cd /d "%~dp0"

if not exist .venv (
  echo Setting up Python environment first time — wait a minute...
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)

echo Starting CS2 Sniper on http://127.0.0.1:8765
echo Keep this window open. Close it to stop the bot.
echo.

start "" cmd /c "timeout /t 5 /nobreak >nul && start http://127.0.0.1:8765"

python -m server.main
