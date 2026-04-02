@echo off
cd /d "%~dp0\.."
if not exist "output" (
    echo No output folder found. Run the scraper first.
    pause
    exit /b 1
)
start "" "output"
