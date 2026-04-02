"""Allows running the scraper as ``python -m src``."""

import asyncio

from src.cli import main

asyncio.run(main())
