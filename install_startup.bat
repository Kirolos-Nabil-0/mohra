@echo off
title Install Mohra Automation to Windows Startup
echo ========================================================
echo   Installing Mohra Automation to Windows Startup
echo ========================================================
echo.

set "TARGET_DIR=%~dp0"
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_VBS=%STARTUP_FOLDER%\MohraAutomation.vbs"

if not exist "%TARGET_DIR%venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found. Running setup.bat first...
    call "%TARGET_DIR%setup.bat"
)

echo Creating Windows Startup launcher at:
echo "%STARTUP_VBS%"
echo.

(
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.CurrentDirectory = "%TARGET_DIR:~0,-1%"
    echo WshShell.Run "wscript.exe """ ^& "%TARGET_DIR%silent_start.vbs"""", 0, False
) > "%STARTUP_VBS%"

if exist "%STARTUP_VBS%" (
    echo [SUCCESS] Mohra Automation successfully added to Windows Startup!
    echo It will automatically launch silently in the background on every PC boot.
    echo.
    echo Starting background service right now...
    wscript.exe "%STARTUP_VBS%"
    echo.
    echo [ACTIVE] The app is now running in the background!
    echo Check the Windows System Tray (near the clock) for the Mohra icon.
) else (
    echo [ERROR] Failed to write to Startup folder.
)

echo.
echo ========================================================
pause
