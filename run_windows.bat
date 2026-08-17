@echo off
setlocal
cd /d "%~dp0"
title MOEX Risk Dashboard 1.0 Deployment Ready

if not exist .venv (
  echo Creating virtual environment...
  py -m venv .venv
)

call .venv\Scripts\activate
if errorlevel 1 (
  echo Failed to activate .venv
  pause
  exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  pause
  exit /b 1
)

echo.
python launcher.py

echo.
echo Dashboard stopped.
pause
