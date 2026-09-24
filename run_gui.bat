@echo off
title Mohra Automation - GUI
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found!
    echo Running setup.bat first...
    call setup.bat
)

call venv\Scripts\activate
python gui.py
