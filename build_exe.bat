@echo off
setlocal enabledelayedexpansion
title Mohra Automation - Build Windows Executable (.exe)
echo ========================================================
echo        Mohra Automation - Windows Packaging Tool
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)

echo [1/6] Checking virtual environment...
if not exist "venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate

echo [2/6] Installing dependencies and PyInstaller...
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo [3/6] Ensuring Playwright browser binaries are installed...
playwright install chromium

echo [4/6] Generating app icon if missing...
if not exist "assets\app.ico" (
    python -c "import os; from PIL import Image, ImageDraw; os.makedirs('assets', exist_ok=True); img = Image.new('RGBA', (256, 256), (0, 0, 0, 0)); dc = ImageDraw.Draw(img); dc.ellipse([16, 16, 240, 240], fill=(0, 168, 204, 255), outline=(255, 255, 255, 255), width=8); p = [(64, 180), (64, 82), (128, 138), (192, 82), (192, 180)]; dc.line(p, fill=(255, 255, 255, 255), width=16); img.save('assets/app.ico', format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])"
)

echo [5/6] Building Executable with PyInstaller...
pyinstaller --noconfirm --clean mohra.spec
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

echo [6/6] Preparing distribution folders and assets...
if not exist "dist\Mohra\logs" mkdir "dist\Mohra\logs"
if not exist "dist\Mohra\cache" mkdir "dist\Mohra\cache"
if not exist "dist\Mohra\chrome_profile" mkdir "dist\Mohra\chrome_profile"

if exist "config.json" (
    copy /y "config.json" "dist\Mohra\config.json" >nul
) else if not exist "dist\Mohra\config.json" (
    if exist "dist\Mohra\config.example.json" (
        copy /y "dist\Mohra\config.example.json" "dist\Mohra\config.json" >nul
    ) else (
        copy /y "config.example.json" "dist\Mohra\config.json" >nul
    )
)

echo Packaging into ZIP archive for easy distribution...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\Mohra\*' -DestinationPath 'dist\Mohra-Windows.zip' -Force"

echo.
echo ========================================================
echo   SUCCESS! Packaging Complete.
echo ========================================================
echo   Executable Folder:  dist\Mohra\
echo   Main Executable:    dist\Mohra\Mohra.exe
echo   Zip Package:        dist\Mohra-Windows.zip
echo ========================================================
echo.
pause
