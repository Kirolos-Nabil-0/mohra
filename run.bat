@echo off
title Mohra Automation - CLI
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found!
    echo Running setup.bat first...
    call setup.bat
)

call venv\Scripts\activate
python mohra_app.py %*
pause
