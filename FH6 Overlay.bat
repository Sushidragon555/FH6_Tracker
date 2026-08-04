@echo off
rem ============================================================
rem  FH6 Overlay - standalone mode (no GUI window)
rem  Shows only the live telemetry overlay over the game.
rem  Quit anytime with Ctrl+Alt+Q. F6 still records races.
rem ============================================================
cd /d "%~dp0"
if not exist ".deps_installed" (
    echo Setting up the FH6 Tracker for the first time...
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
start "" pythonw overlay.py
exit /b 0
