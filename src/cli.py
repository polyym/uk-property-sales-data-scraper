"""Command-line interface: argument parsing, logging, and run modes."""

import argparse
import asyncio
import csv
import logging
import math
import os
import sys
import time
from datetime import datetime

import polars as pl
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from src.config import BROWSER_PROFILES
from src.config import COLUMNS
from src.config import DIR_DEBUG_HTML
from src.config import DIR_LOGS
from src.config import DIR_OUTPUT
from src.config import EXCLUDED_LISTING_TYPES
from src.config import EXCLUDED_POSTCODE_PREFIXES
from src.config import MIN_PRICE
from src.config import NUM_TABS
from src.config import NUM_TABS_PHASE1
from src.config import RAW_COLUMNS
from src.config import SITE_URL
from src.config import TEST_LIMIT
from src.config import WORKERS_PER_PROXY
from src.config import _LOCATION_NAME
from src.config import _config
from src.config import __version__
from src.config import log
from src.clean import clean_output
from src.collect import collect_all_property_ids
from src.collect import collect_ids_via_proxies
from src.debug import run_debug
from src.proxy import load_proxies
from src.proxy import setup_proxy_pool
from src.scrape import scrape_tab
from src.scrape import scrape_tab_from_queue
from src.scrape import scrape_with_proxy


def _validate_url(url: str) -> bool:
    """Validates that the URL looks like a property search page."""
    if not url.startswith('https://'):
        log.error('The URL must start with https://')
        return False
    if '.' not in url.split('//')[1]:
        log.error('The URL does not appear to be valid.')
        return False
    return True


def _fmt_time(seconds: float) -> str:
    """Formats seconds as a human-readable ``Xm Ys`` string."""
    m, s = divmod(int(seconds), 60)
    return f'{m}m {s}s'


def _print_summary(
    phase1: float, phase2: float, phase3: float,
    saved: int, failed: int,
    raw_path: str, clean_path: str,
) -> None:
    """Logs an ASCII summary box with scrape results and timing."""
    total = phase1 + phase2 + phase3
    log.info('')
    log.info('  +----------------------------------------------+')
    log.info('  |             SCRAPE COMPLETE                  |')
    log.info('  +----------------------------------------------+')
    log.info(f'  |  Properties saved:  {saved:<24} |')
    log.info(f'  |  Failed:            {failed:<24} |')
    log.info(f'  |  Phase 1 (IDs):     {_fmt_time(phase1):<24} |')
    log.info(f'  |  Phase 2 (scrape):  {_fmt_time(phase2):<24} |')
    log.info(f'  |  Phase 3 (clean):   {_fmt_time(phase3):<24} |')
    log.info(f'  |  Total:             {_fmt_time(total):<24} |')
    log.info('  +----------------------------------------------+')
    log.info(f'  |  Raw CSV:    {os.path.basename(raw_path):<32}|')
    log.info(f'  |  Clean CSV:  {os.path.basename(clean_path):<32}|')
    log.info('  +----------------------------------------------+')
    log.info('')
    log.info('  Files saved to the "output" folder.')


async def main() -> None:
    """Entry point: parses arguments and orchestrates the scrape."""
    parser = argparse.ArgumentParser(
        description='Property Scraper',
    )
    parser.add_argument(
        'url', nargs='?', default=None,
        help='The initial search page URL.',
    )
    parser.add_argument(
        '--test', action='store_true',
        help=f'Test mode: collect only {TEST_LIMIT} properties.',
    )
    parser.add_argument(
        '--ids-file',
        help=(
            'Path to a previously saved IDs file to skip '
            'Phase 1 and run Phase 2 directly.'
        ),
    )
    parser.add_argument(
        '--clean-file',
        help=(
            'Path to a raw CSV file to run Phase 3 (cleaning) '
            'only, skipping Phases 1 and 2.'
        ),
    )
    parser.add_argument(
        '--xor-key', type=int, default=None,
        help=(
            'Integer key for XOR obfuscation of property IDs '
            'in the clean output. If omitted, uses the value '
            'from config.json. If 0, IDs are not obfuscated.'
        ),
    )
    parser.add_argument(
        '--debug', action='store_true',
        help=(
            'Debug mode: fetch one search page and one detail '
            'page, save HTML, and validate extraction logic.'
        ),
    )
    parser.add_argument(
        '--version', action='version',
        version=f'%(prog)s {__version__}',
    )
    args = parser.parse_args()

    if (not args.debug and not args.url
            and not args.ids_file and not args.clean_file):
        parser.error(
            'either url, --ids-file, --clean-file, or '
            '--debug is required'
        )

    # Resolve XOR key: CLI flag > config.json > 0 (disabled).
    if args.xor_key is None:
        args.xor_key = _config.get('xor_key', 0)

    for d in (DIR_OUTPUT, DIR_LOGS, DIR_DEBUG_HTML):
        os.makedirs(d, exist_ok=True)

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    if args.debug:
        mode_label = 'debug'
    elif args.clean_file:
        mode_label = 'clean'
    elif args.test:
        mode_label = 'test'
    else:
        mode_label = 'full'

    log_path = os.path.join(
        DIR_LOGS, f'log_{mode_label}_{timestamp}.txt',
    )

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(
        log_path, mode='w', encoding='utf-8',
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    log.setLevel(logging.DEBUG)
    log.addHandler(console_handler)
    log.addHandler(file_handler)

    try:
        if args.debug:
            await run_debug(args, timestamp)
        elif args.clean_file:
            _run_clean_only(args, timestamp)
        else:
            await _run_scrape(args, timestamp)
    except KeyboardInterrupt:
        log.warning('\n\n*** ABORTED BY USER ***')
        log.info('Any data scraped so far has been saved to the output folder.')
        log.info('You can resume later using option 4 (Resume from saved IDs).')
    except Exception as e:
        log.error(f'\n\n*** ERROR: {e} ***')
        raise
    finally:
        log.info(f'\nLog saved to {log_path}')
        for handler in log.handlers[:]:
            handler.close()
            log.removeHandler(handler)


def _run_clean_only(
    args: argparse.Namespace,
    timestamp: str,
) -> None:
    """Runs Phase 3 only on a previously saved raw CSV.

    Reads the raw CSV, applies all cleaning/filtering, and writes
    the clean CSV to the output directory.

    Args:
        args: Parsed command-line arguments (uses args.clean_file).
        timestamp: Run timestamp for output filenames.
    """
    raw_path = args.clean_file
    if not os.path.exists(raw_path):
        log.error(f'File not found: {raw_path}')
        return

    output_path = os.path.join(
        DIR_OUTPUT, f'cleaned_output_clean_{timestamp}.csv',
    )
    clean_count = clean_output(raw_path, output_path, args.xor_key)
    log.info('\n--- Results ---')
    log.info(f'  Input: {raw_path}')
    log.info(f'  Clean output: {output_path} ({clean_count} rows)')


async def _run_scrape(
    args: argparse.Namespace,
    timestamp: str,
) -> None:
    """Runs the full scrape pipeline (Phases 1-3).

    Args:
        args: Parsed command-line arguments.
        timestamp: Run timestamp for output filenames.
    """
    # Config validation.
    if not _config.get('search_url') and not args.url and not args.ids_file:
        log.error(
            'No config.json found or search_url is missing. '
            'Copy config.example.json to config.json and fill '
            'in your search URL.'
        )
        return

    if args.url and not _validate_url(args.url):
        return

    mode_label = 'test' if args.test else 'full'
    output_path = os.path.join(
        DIR_OUTPUT, f'cleaned_output_{mode_label}_{timestamp}.csv',
    )

    if args.test:
        log.info(
            f'*** TEST MODE: Will collect only '
            f'{TEST_LIMIT} properties ***\n'
        )

    log.info(f'Location: {_LOCATION_NAME}')
    log.info(f'Minimum price filter: \u00a3{MIN_PRICE:,}')
    log.info(f'Excluding listing types: {", ".join(EXCLUDED_LISTING_TYPES)}')
    log.info(f'Excluding postcode prefixes: {", ".join(EXCLUDED_POSTCODE_PREFIXES)}')

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = await browser.new_context(
            user_agent=BROWSER_PROFILES[0]['user_agent'],
            viewport={'width': 1920, 'height': 1080},
        )

        # Proxy setup (shared by both phases).
        raw_proxies = load_proxies()
        harvest_url = (
            args.url if args.url else SITE_URL + '/'
        )

        if raw_proxies:
            proxies, cookie_store, stop_event, refresh_task = (
                await setup_proxy_pool(
                    browser, raw_proxies, harvest_url,
                )
            )
            use_proxies = len(proxies) > 0
        else:
            proxies = []
            cookie_store = {}
            stop_event = None
            refresh_task = None
            use_proxies = False

        # Phase 1: Collect property IDs (or load from file).
        if args.ids_file:
            log.info(
                f'--- Phase 1: Loading IDs from '
                f'{args.ids_file} ---'
            )
            phase1_start = time.time()
            with open(args.ids_file, encoding='utf-8') as f:
                property_ids = [
                    line.strip() for line in f
                    if line.strip()
                ]
            phase1_elapsed = time.time() - phase1_start
            log.info(
                f'Loaded {len(property_ids)} IDs from file'
            )
        elif use_proxies:
            log.info(f'Starting extraction on: {args.url}')
            log.info('\n--- Phase 1: Collecting property IDs '
                     '(proxies + local browser) ---')
            phase1_start = time.time()

            property_ids = await collect_ids_via_proxies(
                proxies, cookie_store, args.url,
                context=context,
                test_mode=args.test,
            )

            phase1_elapsed = time.time() - phase1_start
            log.info(
                f'Phase 1 completed in '
                f'{int(phase1_elapsed // 60)}m '
                f'{int(phase1_elapsed % 60)}s'
            )

            if property_ids:
                ids_path = os.path.join(
                    DIR_OUTPUT,
                    f'ids_{mode_label}_{timestamp}.txt',
                )
                with open(
                    ids_path, 'w', encoding='utf-8',
                ) as f:
                    f.write('\n'.join(property_ids))
                log.info(f'IDs saved to {ids_path}')
        else:
            log.info(f'Starting extraction on: {args.url}')
            log.info(
                f'Parallel tabs (Phase 1): '
                f'{NUM_TABS_PHASE1}'
            )
            log.info('\n--- Phase 1: Collecting property IDs '
                     '(browser) ---')
            phase1_start = time.time()

            property_ids = await collect_all_property_ids(
                context, args.url, test_mode=args.test,
            )

            phase1_elapsed = time.time() - phase1_start
            log.info(
                f'Phase 1 completed in '
                f'{int(phase1_elapsed // 60)}m '
                f'{int(phase1_elapsed % 60)}s'
            )

            if property_ids:
                ids_path = os.path.join(
                    DIR_OUTPUT,
                    f'ids_{mode_label}_{timestamp}.txt',
                )
                with open(
                    ids_path, 'w', encoding='utf-8',
                ) as f:
                    f.write('\n'.join(property_ids))
                log.info(f'IDs saved to {ids_path}')

        if not property_ids:
            log.error('No property IDs found. Exiting.')
            if stop_event:
                stop_event.set()
                await refresh_task
            await browser.close()
            return

        # Phase 2: Scrape detail pages into raw CSV.
        total = len(property_ids)
        raw_path = os.path.join(
            DIR_OUTPUT, f'raw_output_{mode_label}_{timestamp}.csv',
        )
        counters = {
            'saved': 0,
            'failed': 0,
        }
        counters_lock = asyncio.Lock()
        csv_lock = asyncio.Lock()

        raw_file = open(
            raw_path, 'w', newline='', encoding='utf-8',
        )
        csv_writer = csv.DictWriter(
            raw_file, fieldnames=RAW_COLUMNS,
        )
        csv_writer.writeheader()
        raw_file.flush()
        # Attach file handle so workers can flush after writes.
        csv_writer._file = raw_file

        try:
            if use_proxies:
                local_tabs = min(NUM_TABS, total)
                proxy_workers = len(proxies) * WORKERS_PER_PROXY
                total_workers = proxy_workers + local_tabs
                log.info(
                    f'\n--- Phase 2: Scraping {total} detail'
                    f' pages via {len(proxies)} proxies '
                    f'({WORKERS_PER_PROXY} workers/proxy) '
                    f'+ {local_tabs} local browser tabs '
                    f'({total_workers} total) ---'
                )
                phase2_start = time.time()

                queue: asyncio.Queue[str] = asyncio.Queue()
                for pid in property_ids:
                    await queue.put(pid)

                fallback_lock = asyncio.Lock()
                dead_proxies: set[str] = set()

                tasks = []
                for proxy in proxies:
                    for _ in range(WORKERS_PER_PROXY):
                        tasks.append(
                            scrape_with_proxy(
                                proxy,
                                cookie_store,
                                queue,
                                csv_writer,
                                csv_lock,
                                counters,
                                counters_lock,
                                context,
                                fallback_lock,
                                dead_proxies,
                            ),
                        )

                # Add local browser tabs pulling from the
                # same queue (uses your own IP).
                tab_pages = []
                for tab_id in range(1, local_tabs + 1):
                    tab_page = await context.new_page()
                    await Stealth().apply_stealth_async(tab_page)
                    tab_pages.append(tab_page)
                    tasks.append(
                        scrape_tab_from_queue(
                            tab_id, tab_page, queue,
                            csv_writer, csv_lock,
                            counters, counters_lock,
                        ),
                    )

                await asyncio.gather(*tasks)

                for tab_page in tab_pages:
                    await tab_page.close()

                # Count any items left in the queue (e.g.
                # if all proxies died) as failed.
                remaining = 0
                while not queue.empty():
                    try:
                        queue.get_nowait()
                        remaining += 1
                    except asyncio.QueueEmpty:
                        break
                if remaining:
                    log.warning(
                        f'\n  {remaining} properties could'
                        f' not be processed (all proxies'
                        f' failed)'
                    )
                    counters['failed'] += remaining

            else:
                num_tabs = min(NUM_TABS, total)
                batch_size = math.ceil(total / num_tabs)
                batches = [
                    property_ids[i:i + batch_size]
                    for i in range(0, total, batch_size)
                ]

                log.info(
                    f'\n--- Phase 2: Scraping {total} detail'
                    f' pages across {len(batches)} tabs ---'
                )
                phase2_start = time.time()

                tab_pages = []
                tasks = []
                for tab_id, batch in enumerate(
                    batches, start=1,
                ):
                    tab_page = await context.new_page()
                    await Stealth().apply_stealth_async(tab_page)
                    tab_pages.append(tab_page)
                    tasks.append(
                        scrape_tab(
                            tab_id, tab_page, batch,
                            csv_writer, csv_lock,
                            counters, counters_lock,
                        ),
                    )

                await asyncio.gather(*tasks)

                for tab_page in tab_pages:
                    await tab_page.close()

            phase2_elapsed = time.time() - phase2_start
        finally:
            raw_file.close()
            log.debug(f'  Raw CSV closed: {raw_path}')

        if stop_event:
            stop_event.set()
            await refresh_task

        await browser.close()

    log.info('\n--- Phase 2 Results ---')
    log.info(f'  Phase 1 (ID collection): '
             f'{int(phase1_elapsed // 60)}m '
             f'{int(phase1_elapsed % 60)}s')
    log.info(f'  Phase 2 (detail scraping): '
             f'{int(phase2_elapsed // 60)}m '
             f'{int(phase2_elapsed % 60)}s')
    log.info(f'  Raw rows saved: {counters["saved"]}')
    log.info(f'  Failed to extract: {counters["failed"]}')
    log.info(f'  Raw output saved to {raw_path}')

    # Phase 3: Clean the raw output.
    if counters['saved'] > 0:
        phase3_start = time.time()
        clean_count = clean_output(raw_path, output_path, args.xor_key)
        phase3_elapsed = time.time() - phase3_start

        _print_summary(
            phase1_elapsed, phase2_elapsed, phase3_elapsed,
            counters['saved'], counters['failed'],
            raw_path, output_path,
        )
    else:
        pl.DataFrame(
            schema={col: pl.Utf8 for col in COLUMNS},
        ).write_csv(output_path)
        log.info('\n--- Results ---')
        log.info('  No properties were scraped successfully.')
        log.info(f'  Empty output saved to {output_path}')
