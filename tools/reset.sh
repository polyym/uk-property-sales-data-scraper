#!/bin/bash
cd "$(dirname "$0")/.."
echo ""
echo "  This will delete the virtual environment so it can be"
echo "  recreated on next run. Output files will NOT be deleted."
echo ""
read -p "  Are you sure? (y/n): " confirm
if [ "$confirm" != "y" ]; then exit 0; fi
if [ -d "venv" ]; then
    rm -rf venv
    echo "  Virtual environment deleted."
else
    echo "  No virtual environment found."
fi
echo "  Run the scraper again to reinstall everything."
read -p "  Press any key to continue..."
