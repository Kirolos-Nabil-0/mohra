@echo off
title Mohra Automation - Update
echo ========================================================
echo   Checking & Applying Updates for Mohra Automation
echo ========================================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found!
    pause
    exit /b 1
)

call venv\Scripts\activate
python -c "from modules.updater import AutoUpdater, log_updater; from config import load_config; u = AutoUpdater(load_config()); has, info = u.check_for_updates(); print(f'Update check: {info}'); u.check_and_apply_update_silently() if has else print('App is already up to date.')"

echo.
echo ========================================================
echo [DONE] Update check complete.
echo ========================================================
pause
