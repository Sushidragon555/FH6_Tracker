@echo off
title FH6 Tracker Setup
color 0B
echo.
echo  ============================================
echo   FH6 Tracker - First Time Setup
echo  ============================================
echo.
echo  This will install everything you need and
echo  launch the app. You only need to run this
echo  once.
echo.

cd /d "%~dp0"

rem --- Check Python is installed ---
python --version >nul 2>&1
if errorlevel 1 (
    echo  [X] Python is not installed or not in PATH.
    echo.
    echo  Download Python from: https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)

rem --- Install dependencies ---
if not exist ".deps_installed" (
    echo  [1/3] Installing required packages...
    python -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo  [X] Failed to install packages. Check your internet connection.
        pause
        exit /b 1
    )
    echo done > ".deps_installed"
    echo  [1/3] Done!
) else (
    echo  [1/3] Packages already installed, skipping...
)

echo.
echo  [2/3] Checking for updates...
git pull --ff-only >nul 2>nul
if not errorlevel 1 (
    echo  [2/3] Updated to latest version.
) else (
    echo  [2/3] No updates available (or git not installed).
)

echo.
echo  [3/3] Launching FH6 Tracker...
echo.
echo  ============================================
echo   The app will open now. Enjoy!
echo  ============================================
echo.

start "" pythonw fh6_gui.py
exit /b 0
