#!/usr/bin/env python3
"""Entry point; delegates to src.cli.main()."""

import asyncio

from src.cli import main

if __name__ == '__main__':
    asyncio.run(main())
