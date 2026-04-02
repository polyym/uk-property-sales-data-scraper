"""Debug mode: fetches pages, saves HTML, and validates extraction."""

import argparse
import json
import os
import re

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from src.config import BROWSER_PROFILES
from src.config import DETAIL_PAGE_URL_PREFIX
from src.config import DIR_DEBUG_HTML
from src.config import _config
from src.config import log
from src.extract import extract_listing_ids
from src.extract import extract_raw_property_data
from src.extract import extract_result_count
from src.proxy import _wait_for_cf_challenge


async def run_debug(
    args: argparse.Namespace,
    timestamp: str,
) -> None:
    """Fetches pages, saves HTML, and validates extraction logic.

    Fetches a search results page (unsegmented and segmented by price),
    then a detail page for the first property found. Saves all HTML to
    ``logs/html_pages_for_debug/`` and runs the full extraction pipeline
    against each page, reporting what was found and what failed.

    Args:
        args: Parsed command-line arguments.
        timestamp: Run timestamp for output filenames.
    """
    search_url = args.url or _config.get('search_url', '')
    if not search_url:
        log.error(
            'No URL provided and no search_url in config.json. '
            'Pass a URL argument or set search_url in config.json.'
        )
        return

    log.info('*** DEBUG MODE ***\n')

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled'],
        )
        try:
            context = await browser.new_context(
                user_agent=BROWSER_PROFILES[0]['user_agent'],
                viewport={'width': 1920, 'height': 1080},
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            # -- 1. Search results page --------------------------
            log.info('=' * 60)
            log.info('FETCHING SEARCH RESULTS PAGE')
            log.info('=' * 60)
            await page.goto(
                search_url, wait_until='domcontentloaded',
            )
            await page.wait_for_timeout(7000)
            await _wait_for_cf_challenge(page)
            search_html = await page.content()

            search_path = os.path.join(
                DIR_DEBUG_HTML,
                f'debug_search_{timestamp}.html',
            )
            with open(search_path, 'w', encoding='utf-8') as f:
                f.write(search_html)
            log.info(
                f'Saved: {search_path} ({len(search_html):,} chars)'
            )

            # Result count.
            result_count = extract_result_count(search_html)
            if result_count:
                log.info(f'Result count: {result_count:,}')
            else:
                log.warning('Could not find result count')
                alt = re.search(
                    r'(\d[\d,]+)\s*results', search_html,
                )
                if alt:
                    log.info(
                        f'  Alternative match: {alt.group(0)}'
                    )

            # ID extraction.
            ids = extract_listing_ids(search_html)
            log.info(f'Extracted IDs: {len(ids)}')
            if ids:
                log.info(f'Sample IDs: {ids[:5]}')
            else:
                log.warning('No IDs found!')
                detail_links = re.findall(
                    r'href="([^"]*details[^"]*)"', search_html,
                )
                if detail_links:
                    log.info(
                        f'Links containing "details": '
                        f'{detail_links[:10]}'
                    )

            # -- 2. Segmented search page -------------------------
            sep = '&' if '?' in search_url else '?'
            segment_url = (
                f'{search_url}{sep}price_min=100000'
                f'&price_max=200000&view_type=list'
            )
            log.info('')
            log.info('=' * 60)
            log.info('FETCHING SEGMENTED SEARCH PAGE (100k-200k)')
            log.info('=' * 60)
            await page.goto(
                segment_url, wait_until='domcontentloaded',
            )
            await page.wait_for_timeout(7000)
            await _wait_for_cf_challenge(page)
            seg_html = await page.content()

            seg_path = os.path.join(
                DIR_DEBUG_HTML,
                f'debug_search_segmented_{timestamp}.html',
            )
            with open(seg_path, 'w', encoding='utf-8') as f:
                f.write(seg_html)
            log.info(
                f'Saved: {seg_path} ({len(seg_html):,} chars)'
            )

            seg_count = extract_result_count(seg_html)
            if seg_count:
                log.info(f'Segment result count: {seg_count:,}')
            else:
                log.warning(
                    'Could not find segment result count'
                )

            seg_ids = extract_listing_ids(seg_html)
            log.info(f'Segment IDs: {len(seg_ids)}')
            if seg_ids:
                log.info(f'Sample IDs: {seg_ids[:5]}')

            # Page 2 of segment.
            seg_page2_url = f'{segment_url}&pn=2'
            log.info('')
            log.info('Fetching segment page 2...')
            await page.goto(
                seg_page2_url, wait_until='domcontentloaded',
            )
            await page.wait_for_timeout(7000)
            await _wait_for_cf_challenge(page)
            seg_p2_html = await page.content()

            seg_p2_path = os.path.join(
                DIR_DEBUG_HTML,
                f'debug_search_segmented_p2_{timestamp}.html',
            )
            with open(seg_p2_path, 'w', encoding='utf-8') as f:
                f.write(seg_p2_html)
            log.info(
                f'Saved: {seg_p2_path} '
                f'({len(seg_p2_html):,} chars)'
            )

            seg_p2_ids = extract_listing_ids(seg_p2_html)
            log.info(f'Page 2 IDs: {len(seg_p2_ids)}')
            if seg_p2_ids:
                log.info(f'Sample: {seg_p2_ids[:5]}')

            # -- 3. Detail page -----------------------------------
            all_ids = ids or seg_ids
            if not all_ids:
                log.error(
                    'No property IDs found on any search page. '
                    'Cannot fetch a detail page.'
                )
                return

            detail_id = all_ids[0]
            detail_url = f'{DETAIL_PAGE_URL_PREFIX}{detail_id}/'
            log.info('')
            log.info('=' * 60)
            log.info(f'FETCHING DETAIL PAGE: {detail_id}')
            log.info('=' * 60)
            await page.goto(
                detail_url, wait_until='domcontentloaded',
            )
            await page.wait_for_timeout(7000)
            await _wait_for_cf_challenge(page)
            detail_html = await page.content()

            detail_path = os.path.join(
                DIR_DEBUG_HTML,
                f'debug_detail_{detail_id}_{timestamp}.html',
            )
            with open(detail_path, 'w', encoding='utf-8') as f:
                f.write(detail_html)
            log.info(
                f'Saved: {detail_path} '
                f'({len(detail_html):,} chars)'
            )

            # Run full raw extraction and report results.
            result = extract_raw_property_data(
                detail_html, detail_id,
            )
            if result:
                log.info('')
                log.info('--- Extraction results ---')
                for key, value in result.items():
                    display = str(value)
                    if len(display) > 120:
                        display = display[:120] + '...'
                    log.info(f'  {key}: {display}')
            else:
                log.error(
                    f'extract_raw_property_data returned None '
                    f'for {detail_id}'
                )

                # Fall back to manual checks for diagnostics.
                soup = BeautifulSoup(
                    detail_html, 'html.parser',
                )

                jsonld_found = False
                for tag in soup.find_all(
                    'script', type='application/ld+json',
                ):
                    try:
                        data = json.loads(tag.string)
                        if (isinstance(data, dict)
                                and data.get('@type')
                                == 'RealEstateListing'):
                            jsonld_found = True
                            log.info(
                                'JSON-LD RealEstateListing: FOUND'
                            )
                            break
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not jsonld_found:
                    log.warning(
                        'No JSON-LD RealEstateListing found'
                        ' in HTML'
                    )

                h1 = soup.find('h1')
                log.info(
                    f'H1: '
                    f'{h1.get_text(strip=True)[:80] if h1 else "NOT FOUND"}'
                )

                addr = soup.find('address')
                log.info(
                    f'Address: '
                    f'{addr.get_text(strip=True)[:80] if addr else "NOT FOUND"}'
                )

                desc = soup.find(id='detailed-desc')
                log.info(
                    f'Description (#detailed-desc): '
                    f'{"FOUND" if desc else "NOT FOUND"}'
                )

        finally:
            await browser.close()

    log.info('')
    log.info('=' * 60)
    log.info('DEBUG COMPLETE')
    log.info('=' * 60)
    log.info(f'HTML files saved to: {DIR_DEBUG_HTML}/')
