@echo off
cd /d "%~dp0\.."
echo.
echo   This will delete the virtual environment so it can be
echo   recreated on next run. Output files will NOT be deleted.
echo.
set /p confirm="   Are you sure? (y/n): "
if /i "%confirm%" neq "y" exit /b 0
if exist "venv" (
    rmdir /s /q venv
    echo   Virtual environment deleted.
) else (
    echo   No virtual environment found.
)
echo   Run the scraper again to reinstall everything.
pause
