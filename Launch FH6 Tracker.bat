@echo off
rem ============================================================
rem  Double-click this file to launch the FH6 Tracker.
rem  On the first run it installs what it needs, then it opens
rem  the app with no black console window.
rem ============================================================
cd /d "%~dp0"

if not exist ".deps_installed" (
    echo Setting up the FH6 Tracker for the first time...
    echo Installing required packages, please wait...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install packages. Make sure Python is installed
        echo and added to PATH, then run this file again.
        pause
        exit /b 1
    )
    echo done > ".deps_installed"
)

start "" pythonw fh6_gui.py
exit /b 0
