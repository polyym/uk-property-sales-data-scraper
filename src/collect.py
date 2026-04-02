"""Phase 1: property ID collection from search results."""

import asyncio
import math
import random

from curl_cffi.requests import AsyncSession as CurlAsyncSession
from playwright.async_api import BrowserContext
from playwright.async_api import Page
from playwright_stealth import Stealth

from src.config import DEAD_PROXY_THRESHOLD
from src.config import NUM_TABS_PHASE1
from src.config import PRICE_BREAKPOINTS
from src.config import TEST_LIMIT
from src.config import WORKERS_PER_PROXY_P1
from src.config import log
from src.extract import extract_listing_ids
from src.extract import extract_result_count
from src.extract import generate_pagination_urls
from src.proxy import _curl_fetch
from src.proxy import _wait_for_cf_challenge


async def collect_all_property_ids(
    context: BrowserContext,
    base_url: str,
    test_mode: bool = False,
) -> list[str]:
    """Collects all property IDs from search results.

    For result sets exceeding 1000, segments by price range using
    parallel tabs to bypass the pagination limit. In test mode,
    stops after collecting ``TEST_LIMIT`` unique IDs.

    Args:
        context: Playwright browser context to create tabs from.
        base_url: The initial search URL.
        test_mode: If True, stop after TEST_LIMIT IDs.

    Returns:
        Deduplicated list of property ID strings.
    """
    page = await context.new_page()
    await Stealth().apply_stealth_async(page)

    await page.goto(base_url, wait_until='domcontentloaded')
    await page.wait_for_timeout(random.randint(5000, 7000))
    await _wait_for_cf_challenge(page)
    html = await page.content()

    total_results = extract_result_count(html)
    first_page_ids = extract_listing_ids(html)
    log.info(f'{total_results} total results')
    log.info(f'{len(first_page_ids)} IDs found on first page')

    await page.close()
    all_ids = list(first_page_ids)

    if test_mode:
        if len(set(all_ids)) < TEST_LIMIT:
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            for i, paged_url in enumerate(
                generate_pagination_urls(
                    total_results, base_url,
                ),
                start=2,
            ):
                if len(set(all_ids)) >= TEST_LIMIT:
                    break
                log.info(f'Page {i}')
                await page.goto(paged_url)
                await page.wait_for_timeout(
                    random.randint(5000, 7000),
                )
                await _wait_for_cf_challenge(page)
                html = await page.content()
                all_ids.extend(extract_listing_ids(html))
            await page.close()
    elif total_results > 1000:
        all_ids = await _collect_segmented_parallel(
            context, base_url,
        )
    else:
        log.info('No need to segment')
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        for i, paged_url in enumerate(
            generate_pagination_urls(total_results, base_url),
            start=2,
        ):
            log.info(f'Page {i}')
            await page.goto(paged_url)
            await page.wait_for_timeout(
                random.randint(5000, 7000),
            )
            await _wait_for_cf_challenge(page)
            html = await page.content()
            all_ids.extend(extract_listing_ids(html))
        await page.close()

    unique_ids = list(dict.fromkeys(all_ids))
    if test_mode:
        unique_ids = unique_ids[:TEST_LIMIT]

    log.info(f'{len(unique_ids)} total unique listings')
    return unique_ids


async def _collect_segment_pages(
    tab_id: int,
    tab_page: Page,
    segments: list[tuple],
    base_url: str,
) -> list[str]:
    """Worker that collects IDs from assigned price segments.

    Each worker processes its batch of price segments sequentially,
    paginating through all pages of each segment. If a segment
    exceeds 1000 results, it is automatically split into smaller
    sub-segments to avoid the pagination cap.

    Args:
        tab_id: Numeric identifier for logging.
        tab_page: Playwright page (browser tab) to use.
        segments: List of (price_min, price_max) tuples.
        base_url: The base search URL.

    Returns:
        List of property ID strings found across all segments.
    """
    await tab_page.wait_for_timeout(random.randint(500, 1500) * tab_id)
    connector = '&' if '?' in base_url else '?'
    ids: list[str] = []

    queue = list(segments)
    while queue:
        price_min, price_max = queue.pop(0)
        segment_url = (
            f'{base_url}{connector}price_min={price_min}'
            f'&price_max={price_max}&view_type=list'
        )

        await tab_page.goto(segment_url)
        await tab_page.wait_for_timeout(
            random.randint(5000, 7000),
        )
        await _wait_for_cf_challenge(tab_page)
        html = await tab_page.content()

        segment_count = extract_result_count(html)
        log.info(
            f'  [P1 Tab {tab_id}] {price_min} >< {price_max}'
            f' - {segment_count} results'
        )

        if segment_count > 1000 and price_max != '':
            midpoint = (int(price_min) + int(price_max)) // 2
            if midpoint != int(price_min):
                log.info(
                    f'  [P1 Tab {tab_id}]   >1000, splitting'
                    f' at {midpoint}'
                )
                queue.insert(0, (midpoint, price_max))
                queue.insert(0, (price_min, midpoint))
                continue

        ids.extend(extract_listing_ids(html))

        for j, paged_url in enumerate(
            generate_pagination_urls(segment_count, segment_url),
            start=2,
        ):
            log.info(f'  [P1 Tab {tab_id}]   Page {j}')
            await tab_page.goto(paged_url)
            await tab_page.wait_for_timeout(
                random.randint(5000, 7000),
            )
            await _wait_for_cf_challenge(tab_page)
            html = await tab_page.content()
            ids.extend(extract_listing_ids(html))

    log.debug(f'  [P1 Tab {tab_id}] Finished - {len(ids)} IDs')
    return ids


async def _collect_segmented_parallel(
    context: BrowserContext,
    base_url: str,
) -> list[str]:
    """Collects property IDs across price segments using parallel tabs.

    Distributes price segments evenly across ``NUM_TABS_PHASE1`` tabs
    and runs them concurrently.

    Args:
        context: Playwright browser context to create tabs from.
        base_url: The base search URL.

    Returns:
        Combined list of property IDs from all segments.
    """
    log.info(
        f'Segmenting by price across '
        f'{NUM_TABS_PHASE1} parallel tabs'
    )

    segments = [
        (PRICE_BREAKPOINTS[i], PRICE_BREAKPOINTS[i + 1])
        for i in range(len(PRICE_BREAKPOINTS) - 1)
    ]

    num_tabs = min(NUM_TABS_PHASE1, len(segments))
    batch_size = math.ceil(len(segments) / num_tabs)
    batches = [
        segments[i:i + batch_size]
        for i in range(0, len(segments), batch_size)
    ]

    tab_pages = []
    tasks = []
    for tab_id, batch in enumerate(batches, start=1):
        tab_page = await context.new_page()
        await Stealth().apply_stealth_async(tab_page)
        tab_pages.append(tab_page)
        tasks.append(
            _collect_segment_pages(
                tab_id, tab_page, batch, base_url,
            ),
        )

    results = await asyncio.gather(*tasks)

    for tab_page in tab_pages:
        await tab_page.close()

    all_ids: list[str] = []
    for id_list in results:
        all_ids.extend(id_list)
    return all_ids


async def _collect_ids_proxy_worker(
    proxy: dict[str, str],
    cookie_store: dict[str, dict[str, str]],
    queue: asyncio.Queue,
    all_ids: list[str],
    ids_lock: asyncio.Lock,
    base_url: str,
    test_mode: bool,
    dead_proxies: set[str],
) -> None:
    """Worker that collects property IDs via curl_cffi.

    Handles two work item types:
    - ``('segment', price_min, price_max)``: Fetches a price segment,
      splits it if >1000 results, or queues pagination pages.
    - ``('page', url)``: Fetches a pagination page and extracts IDs.

    On failure, re-enqueues the item so local browser tabs or
    other proxies can pick it up. Marks the proxy as dead after
    3 consecutive failures and exits.

    Args:
        proxy: Proxy configuration dict.
        cookie_store: Shared dict mapping proxy host to cookies.
        queue: Shared work queue accepting segment and page tuples.
        all_ids: Shared list to append extracted IDs to.
        ids_lock: Lock for thread-safe access to all_ids.
        base_url: The base search URL.
        test_mode: If True, stop after TEST_LIMIT IDs.
        dead_proxies: Shared set of proxy hosts marked as dead.
    """
    proxy_label = proxy['host']
    connector_char = '&' if '?' in base_url else '?'
    consecutive_failures = 0

    async with CurlAsyncSession() as session:
        while True:
            if proxy_label in dead_proxies:
                break

            if test_mode:
                async with ids_lock:
                    if len(set(all_ids)) >= TEST_LIMIT:
                        break

            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=2.0,
                )
            except asyncio.TimeoutError:
                break

            item_type = item[0]

            if item_type == 'segment':
                price_min, price_max = item[1], item[2]
                url = (
                    f'{base_url}{connector_char}'
                    f'price_min={price_min}'
                    f'&price_max={price_max}'
                    f'&view_type=list'
                )
            else:
                url = item[1]

            html = await _curl_fetch(
                session, url, proxy, cookie_store,
            )

            if html is None:
                consecutive_failures += 1
                # Re-enqueue so local tabs can handle it.
                await queue.put(item)
                queue.task_done()
                if consecutive_failures >= DEAD_PROXY_THRESHOLD:
                    log.warning(
                        f'  [P1 {proxy_label}]'
                        f' {DEAD_PROXY_THRESHOLD} consecutive'
                        f' failures, marking as dead'
                    )
                    dead_proxies.add(proxy_label)
                    break
                continue

            consecutive_failures = 0

            ids = extract_listing_ids(html)
            async with ids_lock:
                all_ids.extend(ids)

            if item_type == 'segment':
                count = extract_result_count(html)
                log.info(
                    f'  [P1 {proxy["host"]}] '
                    f'{price_min} >< {price_max}'
                    f' - {count} results'
                )

                if count > 1000 and price_max != '':
                    midpoint = (
                        (int(price_min) + int(price_max)) // 2
                    )
                    if midpoint != int(price_min):
                        log.info(
                            f'  [P1 {proxy["host"]}]'
                            f'   >1000, splitting'
                            f' at {midpoint}'
                        )
                        await queue.put(
                            ('segment', price_min, midpoint),
                        )
                        await queue.put(
                            ('segment', midpoint, price_max),
                        )
                        queue.task_done()
                        continue

                for paged_url in generate_pagination_urls(
                    count, url,
                ):
                    await queue.put(('page', paged_url))

            queue.task_done()


async def _collect_ids_browser_worker(
    tab_id: int,
    tab_page: Page,
    queue: asyncio.Queue,
    all_ids: list[str],
    ids_lock: asyncio.Lock,
    base_url: str,
    test_mode: bool,
) -> None:
    """Browser-tab worker for Phase 1 ID collection.

    Pulls segment/page items from the same shared queue as proxy
    workers, using the local browser (your own IP) to contribute.

    Args:
        tab_id: Numeric identifier for logging.
        tab_page: Playwright page (browser tab) to use.
        queue: Shared work queue accepting segment and page tuples.
        all_ids: Shared list to append extracted IDs to.
        ids_lock: Lock for thread-safe access to all_ids.
        base_url: The base search URL.
        test_mode: If True, stop after TEST_LIMIT IDs.
    """
    connector_char = '&' if '?' in base_url else '?'

    while True:
        if test_mode:
            async with ids_lock:
                if len(set(all_ids)) >= TEST_LIMIT:
                    break

        try:
            item = await asyncio.wait_for(
                queue.get(), timeout=2.0,
            )
        except asyncio.TimeoutError:
            break

        item_type = item[0]

        if item_type == 'segment':
            price_min, price_max = item[1], item[2]
            url = (
                f'{base_url}{connector_char}'
                f'price_min={price_min}'
                f'&price_max={price_max}'
                f'&view_type=list'
            )
        else:
            url = item[1]

        try:
            await tab_page.goto(
                url, wait_until='domcontentloaded',
            )
            await tab_page.wait_for_timeout(
                random.randint(5000, 7000),
            )
            await _wait_for_cf_challenge(tab_page)
            html = await tab_page.content()
        except Exception as e:
            log.warning(
                f'  [P1 local tab {tab_id}] Failed to'
                f' fetch: {e}'
            )
            queue.task_done()
            continue

        ids = extract_listing_ids(html)
        async with ids_lock:
            all_ids.extend(ids)

        if item_type == 'segment':
            count = extract_result_count(html)
            log.info(
                f'  [P1 local tab {tab_id}] '
                f'{price_min} >< {price_max}'
                f' - {count} results'
            )

            if count > 1000 and price_max != '':
                midpoint = (
                    (int(price_min) + int(price_max)) // 2
                )
                if midpoint != int(price_min):
                    log.info(
                        f'  [P1 local tab {tab_id}]'
                        f'   >1000, splitting'
                        f' at {midpoint}'
                    )
                    await queue.put(
                        ('segment', price_min, midpoint),
                    )
                    await queue.put(
                        ('segment', midpoint, price_max),
                    )
                    queue.task_done()
                    continue

            for paged_url in generate_pagination_urls(
                count, url,
            ):
                await queue.put(('page', paged_url))

        queue.task_done()

    log.debug(f'  [P1 local tab {tab_id}] Finished.')


async def collect_ids_via_proxies(
    proxies: list[dict[str, str]],
    cookie_store: dict[str, dict[str, str]],
    base_url: str,
    context: BrowserContext | None = None,
    test_mode: bool = False,
) -> list[str]:
    """Collects property IDs via proxies + local browser tabs.

    Uses a dynamic work queue: price segments may spawn
    sub-segments or pagination pages as workers discover
    result counts. Local browser tabs pull from the same
    queue to utilise the user's own IP.

    Args:
        proxies: List of proxy configuration dicts.
        cookie_store: Shared dict mapping proxy host to cookies.
        base_url: The initial search URL.
        context: Playwright browser context for local tabs.
        test_mode: If True, stop after TEST_LIMIT IDs.

    Returns:
        Deduplicated list of property ID strings.
    """
    # Sort proxies: prefer those with cf_clearance, then by
    # cookie count (most cookies first). Skip cookieless ones.
    ranked = sorted(
        [p for p in proxies
         if cookie_store.get(p['host'])],
        key=lambda p: (
            'cf_clearance' in cookie_store.get(
                p['host'], {},
            ),
            len(cookie_store.get(p['host'], {})),
        ),
        reverse=True,
    )
    html = None
    async with CurlAsyncSession() as session:
        for proxy in ranked:
            html = await _curl_fetch(
                session, base_url, proxy, cookie_store,
            )
            if html:
                break

    if not html:
        log.error('Failed to fetch initial search page')
        return []

    total_results = extract_result_count(html)
    first_ids = extract_listing_ids(html)
    log.info(f'{total_results} total results')
    log.info(f'{len(first_ids)} IDs found on first page')

    all_ids = list(first_ids)
    ids_lock = asyncio.Lock()
    queue: asyncio.Queue = asyncio.Queue()

    if test_mode:
        if len(set(all_ids)) < TEST_LIMIT:
            for url in generate_pagination_urls(
                total_results, base_url,
            ):
                await queue.put(('page', url))
    elif total_results > 1000:
        log.info(
            f'Segmenting by price across '
            f'{len(proxies)} proxies'
        )
        segments = [
            (PRICE_BREAKPOINTS[i], PRICE_BREAKPOINTS[i + 1])
            for i in range(len(PRICE_BREAKPOINTS) - 1)
        ]
        for seg in segments:
            await queue.put(('segment', seg[0], seg[1]))
    else:
        log.info('No need to segment')
        for url in generate_pagination_urls(
            total_results, base_url,
        ):
            await queue.put(('page', url))

    if not queue.empty():
        dead_proxies: set[str] = set()
        tasks = []
        for p in proxies:
            for _ in range(WORKERS_PER_PROXY_P1):
                tasks.append(
                    _collect_ids_proxy_worker(
                        p, cookie_store, queue,
                        all_ids, ids_lock, base_url,
                        test_mode, dead_proxies,
                    ),
                )

        # Add local browser tabs pulling from the same
        # queue (uses your own IP).
        tab_pages = []
        if context:
            num_local = NUM_TABS_PHASE1
            for tab_id in range(1, num_local + 1):
                tab_page = await context.new_page()
                await Stealth().apply_stealth_async(tab_page)
                tab_pages.append(tab_page)
                tasks.append(
                    _collect_ids_browser_worker(
                        tab_id, tab_page, queue,
                        all_ids, ids_lock, base_url,
                        test_mode,
                    ),
                )

        await asyncio.gather(*tasks)

        for tab_page in tab_pages:
            await tab_page.close()

    unique_ids = list(dict.fromkeys(all_ids))
    if test_mode:
        unique_ids = unique_ids[:TEST_LIMIT]

    log.info(f'{len(unique_ids)} total unique listings')
    return unique_ids
