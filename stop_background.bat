@echo off
title Stop Mohra Background Service
echo Stopping Mohra background processes...
taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq Mohra*" >nul 2>nul
wmic process where "commandline like '%%tray_app.py%%' or commandline like '%%background_service.py%%'" call terminate >nul 2>nul
echo [STOPPED] Background service stopped.
timeout /t 2 >nul
