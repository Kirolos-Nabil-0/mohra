@echo off
title Mohra Automation - Setup
echo ========================================================
echo   Setting up Mohra Readora Automation on Windows
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo [1/4] Creating Python Virtual Environment (venv)...
if not exist "venv" (
    python -m venv venv
)

echo [2/4] Upgrading pip...
call venv\Scripts\activate
python -m pip install --upgrade pip

echo [3/4] Installing required libraries (playwright, docx, openpyxl, rich)...
pip install -r requirements.txt

echo [4/4] Installing Chromium browser for Playwright...
playwright install chromium

echo.
echo ========================================================
echo   Setup Complete!
echo   Available Launchers:
echo     - install_startup.bat (Runs in background forever & starts on boot)
echo     - start_background.bat(Runs silently in background right now)
echo     - run_gui.bat         (Desktop Window Interface)
echo     - run.bat             (Command Line Interface)
echo ========================================================
echo.
pause
