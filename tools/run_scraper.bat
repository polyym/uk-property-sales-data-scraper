@echo off
setlocal enabledelayedexpansion
title Property Scraper
cd /d "%~dp0\.."

echo.
echo   ======================================================
echo    Property Scraper v0.1.0
echo   ======================================================
echo.

REM ── 1. Check Python is installed ─────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Python is not installed or not in your PATH.
    echo.
    echo   To fix this:
    echo     1. Go to https://www.python.org/downloads/
    echo     2. Download Python 3.13 or newer
    echo     3. IMPORTANT: Tick "Add Python to PATH" during install
    echo     4. Restart your computer, then try again
    echo.
    pause
    exit /b 1
)

REM ── 2. Check Python version is 3.13+ ─────────────────────
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)
if !PYMAJOR! lss 3 (
    echo   [ERROR] Python !PYVER! found, but 3.13+ is required.
    echo   Please install a newer version from https://www.python.org/downloads/
    pause
    exit /b 1
)
if !PYMAJOR! equ 3 if !PYMINOR! lss 13 (
    echo   [ERROR] Python !PYVER! found, but 3.13+ is required.
    echo   Please install a newer version from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo   Python !PYVER! found.

REM ── 3. Check config.json exists ─────────────────────────
if not exist "config.json" (
    echo.
    echo   [ERROR] config.json not found.
    echo.
    echo   To fix this:
    echo     1. Copy config.example.json to config.json
    echo     2. Edit config.json with your search URL and settings
    echo     3. Run this script again
    echo.
    pause
    exit /b 1
)

REM ── 4. First-run setup ───────────────────────────────────
if not exist "venv" (
    echo.
    echo   ── First-time setup ──────────────────────────────
    echo   This only happens once. It will:
    echo     - Create an isolated Python environment
    echo     - Install required libraries
    echo     - Download a browser for scraping (~150 MB)
    echo   This may take 2-3 minutes. Please wait...
    echo.

    python -m venv venv
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create virtual environment.
        echo   Try deleting the "venv" folder and running again.
        pause
        exit /b 1
    )

    call venv\Scripts\activate.bat
    pip install --upgrade pip >nul 2>&1
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo   [ERROR] Failed to install dependencies.
        echo   Check your internet connection and try again.
        echo   If the problem persists, delete the "venv" folder and retry.
        pause
        exit /b 1
    )

    playwright install chromium
    if %errorlevel% neq 0 (
        echo.
        echo   [ERROR] Failed to download the browser.
        echo   Check your internet connection and try again.
        pause
        exit /b 1
    )

    echo.
    echo   Setup complete.
    echo.
) else (
    call venv\Scripts\activate.bat
    pip install -q -r requirements.txt >nul 2>&1
)

REM ── 5. Read search URL from config ──────────────────────
for /f "usebackq delims=" %%u in (`python -c "import json; print(json.load(open('config.json'))['search_url'])"`) do set "URL=%%u"

REM ── 6. Menu ──────────────────────────────────────────────
:menu
echo.
echo   ── What would you like to do? ───────────────────────
echo.
echo     1) Full scrape       (all properties, ~30 min without proxies)
echo     2) Test scrape       (50 properties only, ~2-3 min)
echo     3) Debug mode        (validate extraction, no CSV output)
echo     4) Resume from file  (skip Phase 1, use saved IDs)
echo     5) Clean only        (re-run Phase 3 on a raw CSV)
echo     6) Custom URL        (enter a different search URL)
echo.
set /p choice="   Select an option (1-6): "

if "%choice%"=="1" goto opt_full
if "%choice%"=="2" goto opt_test
if "%choice%"=="3" goto opt_debug
if "%choice%"=="4" goto opt_resume
if "%choice%"=="5" goto opt_clean
if "%choice%"=="6" goto opt_custom
echo.
echo   Invalid option. Please enter 1, 2, 3, 4, 5, or 6.
goto menu

:opt_full
echo.
echo   Please do not close the Chrome window that appears.
echo.
python scraper.py "%URL%"
goto done

:opt_test
echo.
echo   Please do not close the Chrome window that appears.
echo.
python scraper.py --test "%URL%"
goto done

:opt_debug
echo.
echo   Please do not close the Chrome window that appears.
echo.
python scraper.py --debug "%URL%"
goto done

:opt_resume
echo.
echo   Available ID files:
echo.
dir /b output\ids_*.txt 2>nul
if %errorlevel% neq 0 (
    echo   No saved ID files found. Run a full or test scrape first.
    pause
    exit /b 1
)
echo.
set /p idsfile="   Enter filename: "
echo.
echo   Please do not close the Chrome window that appears.
echo.
python scraper.py --ids-file "output\!idsfile!"
goto done

:opt_clean
echo.
echo   Available raw CSV files:
echo.
dir /b output\raw_output_*.csv 2>nul
if %errorlevel% neq 0 (
    echo   No raw CSV files found. Run a full or test scrape first.
    pause
    exit /b 1
)
echo.
set /p rawfile="   Enter filename: "
python scraper.py --clean-file "output\!rawfile!"
goto done

:opt_custom
echo.
set /p custom_url="   Paste the search URL: "
echo.
echo   Please do not close the Chrome window that appears.
echo.
python scraper.py "!custom_url!"
goto done

:done
echo.
echo   ======================================================
echo    Complete. Check the "output" folder for CSV files.
echo   ======================================================
echo.
pause
