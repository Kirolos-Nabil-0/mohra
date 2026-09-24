@echo off
title Uninstall Mohra Automation from Windows Startup
echo ========================================================
echo   Removing Mohra Automation from Windows Startup
echo ========================================================
echo.

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_VBS=%STARTUP_FOLDER%\MohraAutomation.vbs"

if exist "%STARTUP_VBS%" (
    del "%STARTUP_VBS%"
    echo [REMOVED] Startup file deleted from:
    echo "%STARTUP_VBS%"
) else (
    echo [INFO] Startup file was not found.
)

echo.
echo Stopping any running background instances...
taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq Mohra*" >nul 2>nul
wmic process where "commandline like '%%tray_app.py%%' or commandline like '%%background_service.py%%'" call terminate >nul 2>nul

echo [DONE] Mohra Automation removed from startup and background processes stopped.
echo.
pause
