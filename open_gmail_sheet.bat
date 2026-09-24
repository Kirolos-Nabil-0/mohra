@echo off
title Mohra - Open Chrome Gmail & Sheet
cd /d "%~dp0"

echo ===================================================
echo   Mohra Automation - Open Chrome (Gmail & Sheet)
echo   Account: mohrawagdy58@gmail.com
echo ===================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup.bat first...
    call setup.bat
)

echo Launching Chrome and opening Google Sheet...
venv\Scripts\python.exe -c "from config import load_config; from modules.google_auth import GoogleAuthenticator; ga = GoogleAuthenticator(load_config()); ga.launch_and_login(headless=False)"

pause
