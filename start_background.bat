@echo off
title Start Mohra Background Service
echo Starting Mohra background service silently...
wscript.exe "%~dp0silent_start.vbs"
echo.
echo [ACTIVE] Mohra Automation is now running in the background!
echo Check your system tray (bottom-right near clock).
timeout /t 3 >nul
