#!/bin/bash
cd "$(dirname "$0")/.."

echo ""
echo "  ======================================================"
echo "   Property Scraper v0.1.0"
echo "  ======================================================"
echo ""

# ── 1. Check Python is installed ─────────────────────────
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "  [ERROR] Python is not installed."
    echo ""
    echo "  To fix this:"
    echo "    macOS:  brew install python3"
    echo "    Linux:  sudo apt install python3 python3-venv python3-pip"
    echo ""
    exit 1
fi

# ── 2. Check Python version is 3.13+ ─────────────────────
PY_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 13 ]; }; then
    echo "  [ERROR] Python $PY_VERSION found, but 3.13+ is required."
    echo "  Please install a newer version from https://www.python.org/downloads/"
    exit 1
fi
echo "  Python $PY_VERSION found."

# ── 3. Check config.json exists ───────────────────────────
if [ ! -f "config.json" ]; then
    echo ""
    echo "  [ERROR] config.json not found."
    echo ""
    echo "  To fix this:"
    echo "    1. Copy config.example.json to config.json"
    echo "    2. Edit config.json with your search URL and settings"
    echo "    3. Run this script again"
    echo ""
    exit 1
fi

# ── 4. First-run setup ───────────────────────────────────
if [ ! -d "venv" ]; then
    echo ""
    echo "  ── First-time setup ──────────────────────────────"
    echo "  This only happens once. It will:"
    echo "    - Create an isolated Python environment"
    echo "    - Install required libraries"
    echo "    - Download a browser for scraping (~150 MB)"
    echo "  This may take 2-3 minutes. Please wait..."
    echo ""

    $PYTHON_CMD -m venv venv || {
        echo "  [ERROR] Failed to create virtual environment."
        echo "  Try deleting the 'venv' folder and running again."
        exit 1
    }

    source venv/bin/activate
    pip install --upgrade pip > /dev/null 2>&1
    pip install -r requirements.txt || {
        echo ""
        echo "  [ERROR] Failed to install dependencies."
        echo "  Check your internet connection and try again."
        exit 1
    }

    playwright install chromium || {
        echo ""
        echo "  [ERROR] Failed to download the browser."
        echo "  Check your internet connection and try again."
        exit 1
    }

    echo ""
    echo "  Setup complete."
    echo ""
else
    source venv/bin/activate
    pip install -q -r requirements.txt > /dev/null 2>&1
fi

# ── 5. Read search URL from config ───────────────────────
URL=$($PYTHON_CMD -c "import json; print(json.load(open('config.json'))['search_url'])")

# ── 6. Menu ──────────────────────────────────────────────
while true; do
    echo ""
    echo "  ── What would you like to do? ───────────────────────"
    echo ""
    echo "    1) Full scrape       (all properties, ~30 min without proxies)"
    echo "    2) Test scrape       (50 properties only, ~2-3 min)"
    echo "    3) Debug mode        (validate extraction, no CSV output)"
    echo "    4) Resume from file  (skip Phase 1, use saved IDs)"
    echo "    5) Clean only        (re-run Phase 3 on a raw CSV)"
    echo "    6) Custom URL        (enter a different search URL)"
    echo ""
    read -p "   Select an option (1-6): " choice

    case "$choice" in
        1|2|3|4|5|6) break ;;
        *) echo ""; echo "  Invalid option. Please enter 1, 2, 3, 4, 5, or 6." ;;
    esac
done

case "$choice" in
    1)
        echo ""
        echo "  Please do not close the Chrome window that appears."
        echo ""
        python scraper.py "$URL"
        ;;
    2)
        echo ""
        echo "  Please do not close the Chrome window that appears."
        echo ""
        python scraper.py --test "$URL"
        ;;
    3)
        echo ""
        echo "  Please do not close the Chrome window that appears."
        echo ""
        python scraper.py --debug "$URL"
        ;;
    4)
        echo "  Available ID files:"
        echo ""
        ls -1 output/ids_*.txt 2>/dev/null || {
            echo "  No saved ID files found. Run a full or test scrape first."
            exit 1
        }
        echo ""
        read -p "   Enter filename: " idsfile
        echo ""
        echo "  Please do not close the Chrome window that appears."
        echo ""
        python scraper.py --ids-file "output/$idsfile"
        ;;
    5)
        echo "  Available raw CSV files:"
        echo ""
        ls -1 output/raw_output_*.csv 2>/dev/null || {
            echo "  No raw CSV files found. Run a full or test scrape first."
            exit 1
        }
        echo ""
        read -p "   Enter filename: " rawfile
        python scraper.py --clean-file "output/$rawfile"
        ;;
    6)
        read -p "   Paste the search URL: " custom_url
        echo ""
        echo "  Please do not close the Chrome window that appears."
        echo ""
        python scraper.py "$custom_url"
        ;;
esac

echo ""
echo "  ======================================================"
echo "   Complete. Check the 'output' folder for CSV files."
echo "  ======================================================"
echo ""
read -p "  Press any key to continue..."
