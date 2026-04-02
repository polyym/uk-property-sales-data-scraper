"""Phase 2: detail page scraping via proxies and browser tabs."""

import asyncio
import csv
import random

from curl_cffi.requests import AsyncSession as CurlAsyncSession
from playwright.async_api import BrowserContext
from playwright.async_api import Page
from playwright_stealth import Stealth

from src.config import DEAD_PROXY_THRESHOLD
from src.config import DETAIL_PAGE_URL_PREFIX
from src.config import MAX_RETRIES
from src.config import REQUEST_DELAY_MAX
from src.config import REQUEST_DELAY_MIN
from src.config import log
from src.extract import extract_raw_property_data
from src.proxy import _is_blocked_response
from src.proxy import _wait_for_cf_challenge


async def _browser_fallback(
    context: BrowserContext,
    prop_id: str,
    fallback_lock: asyncio.Lock,
) -> dict[str, str] | None:
    """Falls back to a full browser tab for a single property.

    Uses a lock to prevent multiple concurrent browser fallbacks
    from overloading the browser.

    Args:
        context: Playwright browser context.
        prop_id: Property ID to scrape.
        fallback_lock: Lock to serialise browser fallback access.

    Returns:
        Raw property data dict, or None on failure.
    """
    async with fallback_lock:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        try:
            url = f'{DETAIL_PAGE_URL_PREFIX}{prop_id}/'
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_timeout(random.randint(5000, 8000))
            await _wait_for_cf_challenge(page)
            html = await page.content()
            return extract_raw_property_data(html, prop_id)
        except Exception as e:
            log.warning(f'  [Browser fallback] Failed {prop_id}: {e}')
            return None
        finally:
            await page.close()


async def scrape_with_proxy(
    proxy: dict[str, str],
    cookie_store: dict[str, dict[str, str]],
    queue: asyncio.Queue,
    csv_writer: csv.DictWriter,
    csv_lock: asyncio.Lock,
    counters: dict[str, int],
    counters_lock: asyncio.Lock,
    context: BrowserContext,
    fallback_lock: asyncio.Lock,
    dead_proxies: set[str],
) -> None:
    """Worker that scrapes detail pages via curl_cffi through a proxy.

    Pulls property IDs from a shared queue, fetches pages via
    curl_cffi with harvested cookies, writes raw rows incrementally
    to CSV, and falls back to browser on Cloudflare blocks.

    Args:
        proxy: Proxy configuration dict.
        cookie_store: Shared dict mapping proxy host to cookies
            (updated by cookie refresh task).
        queue: Shared asyncio.Queue of property IDs.
        csv_writer: Shared CSV DictWriter for raw output.
        csv_lock: Async lock for thread-safe CSV writes.
        counters: Shared dict tracking saved/failed counts.
        counters_lock: Async lock for thread-safe counter updates.
        context: Playwright browser context for browser fallback.
        fallback_lock: Lock to serialise browser fallbacks.
        dead_proxies: Set of proxy hosts marked as dead.
    """
    proxy_label = proxy['host']
    proxy_url = proxy['url']
    consecutive_failures = 0

    async with CurlAsyncSession() as session:
        while not queue.empty():
            try:
                prop_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if proxy_label in dead_proxies:
                await queue.put(prop_id)
                queue.task_done()
                break

            detail_url = f'{DETAIL_PAGE_URL_PREFIX}{prop_id}/'

            result = None
            succeeded = False

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await asyncio.sleep(
                        random.uniform(
                            REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
                        ),
                    )
                    cookies = cookie_store.get(proxy_label, {})
                    resp = await session.get(
                        detail_url,
                        proxy=proxy_url,
                        headers={
                            'User-Agent': proxy['user_agent'],
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Accept-Language': 'en-GB,en;q=0.9',
                        },
                        cookies=cookies,
                        timeout=30,
                        impersonate=proxy['impersonate'],
                    )
                    body = resp.text

                    if _is_blocked_response(
                        resp.status_code, body,
                    ):
                        if attempt < MAX_RETRIES:
                            delay = attempt * 3
                            log.warning(
                                f'  [Proxy {proxy_label}]'
                                f' Blocked on {prop_id},'
                                f' retry {attempt}'
                                f'/{MAX_RETRIES}'
                                f' - waiting {delay}s'
                            )
                            await asyncio.sleep(delay)
                            continue
                        # All retries exhausted, try browser.
                        log.warning(
                            f'  [Proxy {proxy_label}]'
                            f' Blocked after {MAX_RETRIES}'
                            f' retries on {prop_id},'
                            f' browser fallback'
                        )
                        result = await _browser_fallback(
                            context, prop_id,
                            fallback_lock,
                        )
                        succeeded = result is not None
                        break

                    if resp.status_code != 200:
                        if attempt < MAX_RETRIES:
                            delay = attempt * 3
                            log.warning(
                                f'  [Proxy {proxy_label}]'
                                f' HTTP {resp.status_code}'
                                f' on {prop_id}, retry'
                                f' {attempt}/{MAX_RETRIES}'
                            )
                            await asyncio.sleep(delay)
                            continue
                        break

                    result = await asyncio.to_thread(
                        extract_raw_property_data,
                        body, prop_id, proxy_url,
                    )
                    succeeded = True
                    break

                except Exception as e:
                    if attempt < MAX_RETRIES:
                        delay = attempt * 3
                        log.warning(
                            f'  [Proxy {proxy_label}]'
                            f' {type(e).__name__} on {prop_id},'
                            f' retry {attempt}/{MAX_RETRIES}'
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            f'  [Proxy {proxy_label}]'
                            f' Failed after {MAX_RETRIES}'
                            f' retries on {prop_id}:'
                            f' {e}'
                        )

            if succeeded:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            if consecutive_failures >= DEAD_PROXY_THRESHOLD:
                log.warning(
                    f'  [Proxy {proxy_label}]'
                    f' {DEAD_PROXY_THRESHOLD} consecutive'
                    f' failures, marking as dead'
                )
                dead_proxies.add(proxy_label)
                await queue.put(prop_id)
                queue.task_done()
                break

            async with counters_lock:
                if result and isinstance(result, dict):
                    async with csv_lock:
                        csv_writer.writerow(result)
                        csv_writer._file.flush()
                    counters['saved'] += 1
                else:
                    counters['failed'] += 1

                processed = sum(counters.values())
                if processed % 100 == 0:
                    log.info(
                        f'  [Progress] {processed} processed'
                        f' ({counters["saved"]} saved,'
                        f' {counters["failed"]} failed)'
                    )

            queue.task_done()

    log.debug(f'  [Proxy {proxy_label}] Worker finished.')


async def scrape_tab(
    tab_id: int,
    tab_page: Page,
    property_ids: list[str],
    csv_writer: csv.DictWriter,
    csv_lock: asyncio.Lock,
    counters: dict[str, int],
    counters_lock: asyncio.Lock,
) -> None:
    """Scrapes property detail pages using a single browser tab.

    Each tab processes its assigned batch of property IDs, extracts
    raw data, and writes rows incrementally to the shared CSV.

    Args:
        tab_id: Numeric identifier for logging.
        tab_page: Playwright page (browser tab) to use.
        property_ids: List of property IDs to scrape.
        csv_writer: Shared CSV DictWriter for raw output.
        csv_lock: Async lock for thread-safe CSV writes.
        counters: Shared dict tracking saved/failed counts.
        counters_lock: Async lock for thread-safe counter updates.
    """
    await tab_page.wait_for_timeout(random.randint(500, 2000) * tab_id)

    for i, prop_id in enumerate(property_ids, start=1):
        log.info(f'  [Tab {tab_id:02d}] {i}/{len(property_ids)}'
                 f' - ID: {prop_id}')

        detail_url = f'{DETAIL_PAGE_URL_PREFIX}{prop_id}/'

        result = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await tab_page.goto(
                    detail_url, wait_until='domcontentloaded',
                )
                await tab_page.wait_for_timeout(
                    random.randint(4000, 6000),
                )
                await _wait_for_cf_challenge(tab_page)

                detail_html = await tab_page.content()
                result = extract_raw_property_data(
                    detail_html, prop_id,
                )
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    delay = attempt * 5
                    log.warning(
                        f'  [Tab {tab_id:02d}] Retry {attempt}'
                        f'/{MAX_RETRIES} for {prop_id}'
                        f' ({type(e).__name__})'
                        f' - waiting {delay}s'
                    )
                    await tab_page.wait_for_timeout(
                        delay * 1000,
                    )
                else:
                    log.error(
                        f'  [Tab {tab_id:02d}] Failed after'
                        f' {MAX_RETRIES} retries on'
                        f' {prop_id}: {e}'
                    )

        async with counters_lock:
            if result and isinstance(result, dict):
                async with csv_lock:
                    csv_writer.writerow(result)
                    csv_writer._file.flush()
                counters['saved'] += 1
            else:
                counters['failed'] += 1

    log.debug(f'  [Tab {tab_id:02d}] Finished.')


async def scrape_tab_from_queue(
    tab_id: int,
    tab_page: Page,
    queue: asyncio.Queue,
    csv_writer: csv.DictWriter,
    csv_lock: asyncio.Lock,
    counters: dict[str, int],
    counters_lock: asyncio.Lock,
) -> None:
    """Scrapes detail pages from a shared queue using a browser tab.

    Unlike ``scrape_tab`` which processes a fixed batch, this worker
    pulls property IDs from the same queue that proxy workers use,
    allowing the local browser to contribute alongside proxies.
    Writes raw rows incrementally to the shared CSV.

    Args:
        tab_id: Numeric identifier for logging.
        tab_page: Playwright page (browser tab) to use.
        queue: Shared asyncio.Queue of property IDs.
        csv_writer: Shared CSV DictWriter for raw output.
        csv_lock: Async lock for thread-safe CSV writes.
        counters: Shared dict tracking saved/failed counts.
        counters_lock: Async lock for thread-safe counter updates.
    """
    while not queue.empty():
        try:
            prop_id = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        detail_url = f'{DETAIL_PAGE_URL_PREFIX}{prop_id}/'

        result = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await tab_page.goto(
                    detail_url,
                    wait_until='domcontentloaded',
                )
                await tab_page.wait_for_timeout(
                    random.randint(4000, 6000),
                )
                await _wait_for_cf_challenge(tab_page)
                detail_html = await tab_page.content()
                result = extract_raw_property_data(
                    detail_html, prop_id,
                )
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    delay = attempt * 5
                    log.warning(
                        f'  [Local tab {tab_id:02d}] Retry'
                        f' {attempt}/{MAX_RETRIES} for'
                        f' {prop_id}'
                        f' ({type(e).__name__})'
                        f' - waiting {delay}s'
                    )
                    await tab_page.wait_for_timeout(
                        delay * 1000,
                    )
                else:
                    log.error(
                        f'  [Local tab {tab_id:02d}] Failed'
                        f' after {MAX_RETRIES} retries on'
                        f' {prop_id}: {e}'
                    )

        async with counters_lock:
            if result and isinstance(result, dict):
                async with csv_lock:
                    csv_writer.writerow(result)
                    csv_writer._file.flush()
                counters['saved'] += 1
            else:
                counters['failed'] += 1

            processed = sum(counters.values())
            if processed % 100 == 0:
                log.info(
                    f'  [Progress] {processed} processed'
                    f' ({counters["saved"]} saved,'
                    f' {counters["failed"]} failed)'
                )

        queue.task_done()

    log.debug(f'  [Local tab {tab_id:02d}] Finished.')
