@echo off
rem ============================================================
rem  Double-click this file to launch the FH6 Tracker.
rem  On the first run it installs what it needs, then it opens
rem  the app with no black console window.
rem  If git is available, it auto-updates from the repo.
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

rem --- Auto-update via git (if available) ---
git pull --ff-only >nul 2>nul
if not errorlevel 1 (
    rem Check if there were actual changes
    git log -1 --oneline > .last_update 2>nul
) else (
    rem git not available or not a repo -- silent skip
)

start "" pythonw fh6_gui.py
exit /b 0
