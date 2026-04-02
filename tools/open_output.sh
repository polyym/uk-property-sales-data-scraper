#!/bin/bash
cd "$(dirname "$0")/.."
if [ ! -d "output" ]; then
    echo "No output folder found. Run the scraper first."
    exit 1
fi
open "output" 2>/dev/null || xdg-open "output" 2>/dev/null || echo "Output folder: $(pwd)/output"
