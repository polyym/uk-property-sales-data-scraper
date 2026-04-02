"""Proxy loading, curl_cffi fetching, and Cloudflare cookie management."""

import asyncio
import os
import random
import time as _time

from curl_cffi.requests import AsyncSession as CurlAsyncSession
from playwright.async_api import Browser
from playwright.async_api import BrowserContext
from playwright.async_api import Page
from playwright_stealth import Stealth

from src.config import BROWSER_PROFILES
from src.config import CF_CHALLENGE_TIMEOUT
from src.config import COOKIE_REFRESH_INTERVAL
from src.config import MAX_PROXIES
from src.config import MAX_RETRIES
from src.config import MIN_COOKIES
from src.config import MIN_PROXIES
from src.config import PROXY_FILE
from src.config import REQUEST_DELAY_MAX
from src.config import REQUEST_DELAY_MIN
from src.config import SITE_URL
from src.config import WORKERS_PER_PROXY
from src.config import log


def load_proxies() -> list[dict[str, str]]:
    """Loads proxy credentials from the proxy file.

    Each line in the file should be formatted as:
    ``ip:port:username:password``

    Returns:
        List of proxy dicts with keys: host, port, username,
        password, url, user_agent, impersonate. Returns an empty
        list if the file is missing or empty.
    """
    if not os.path.exists(PROXY_FILE):
        return []
    proxies = []
    with open(PROXY_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(':', 3)
            if len(parts) != 4:
                continue
            host, port, username, password = parts
            profile = BROWSER_PROFILES[
                len(proxies) % len(BROWSER_PROFILES)
            ]
            proxies.append({
                'host': host,
                'port': port,
                'username': username,
                'password': password,
                'url': f'http://{username}:{password}@{host}:{port}',
                'user_agent': profile['user_agent'],
                'impersonate': profile['impersonate'],
            })
    return proxies


async def _wait_for_cf_challenge(
    page: Page,
    timeout: int = CF_CHALLENGE_TIMEOUT,
) -> bool:
    """Waits for a Cloudflare challenge to resolve, if one is present.

    Polls the page content every 2 seconds. If the page looks like
    a Cloudflare challenge (contains 'Just a moment', 'cf-challenge',
    or 'Checking your browser'), waits up to ``timeout`` ms for it
    to resolve.

    Args:
        page: Playwright page to check.
        timeout: Maximum time in ms to wait for challenge resolution.

    Returns:
        True if page is ready (no challenge or challenge resolved).
        False if challenge did not resolve within the timeout.
    """
    deadline = _time.monotonic() + (timeout / 1000)
    notified = False

    while _time.monotonic() < deadline:
        html = await page.content()
        lower = html.lower()
        is_challenge = (
            len(html) < 100000
            and (
                'just a moment' in lower
                or 'cf-challenge' in lower
                or 'checking your browser' in lower
            )
        )
        if not is_challenge:
            if notified:
                log.info(
                    '  Cloudflare challenge solved, resuming'
                    ' after cooldown.',
                )
                await page.wait_for_timeout(
                    random.randint(8000, 12000),
                )
            return True
        if not notified:
            remaining = int(deadline - _time.monotonic())
            log.info(
                f'  Cloudflare challenge detected, solve it'
                f' in the browser window ({remaining}s timeout).',
            )
            notified = True
        await page.wait_for_timeout(2000)

    log.warning(
        '  Cloudflare challenge did not resolve within timeout.',
    )
    return False


def _is_blocked_response(status: int, body: str) -> bool:
    """Checks whether a response indicates Cloudflare blocking.

    Args:
        status: HTTP status code.
        body: Response body text.

    Returns:
        True if the response looks like a Cloudflare challenge.
    """
    if status == 403:
        return True
    if 'cf-challenge' in body.lower():
        return True
    if 'just a moment' in body.lower() and len(body) < 100000:
        return True
    if 'cloudflare' in body.lower() and len(body) < 100000:
        return True
    return False


async def _curl_fetch(
    session: CurlAsyncSession,
    url: str,
    proxy: dict[str, str],
    cookie_store: dict[str, dict[str, str]],
) -> str | None:
    """Fetches a URL via curl_cffi through a proxy with retry logic.

    Uses Chrome TLS fingerprint impersonation to bypass Cloudflare
    JA3/JA4 fingerprinting.

    Args:
        session: curl_cffi async session.
        url: URL to fetch.
        proxy: Proxy configuration dict.
        cookie_store: Shared dict mapping proxy host to cookies.

    Returns:
        Response body text, or None on failure.
    """
    proxy_label = proxy['host']
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await asyncio.sleep(
                random.uniform(
                    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
                ),
            )
            cookies = cookie_store.get(proxy_label, {})
            resp = await session.get(
                url,
                proxy=proxy['url'],
                headers={
                    'User-Agent': proxy['user_agent'],
                    'Accept': (
                        'text/html,application/xhtml+xml,'
                        'application/xml;q=0.9,*/*;q=0.8'
                    ),
                    'Accept-Language': 'en-GB,en;q=0.9',
                },
                cookies=cookies,
                timeout=30,
                impersonate=proxy['impersonate'],
            )
            body = resp.text
            if _is_blocked_response(resp.status_code, body):
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 3)
                    continue
                log.warning(
                    f'  [Proxy {proxy_label}] Blocked by'
                    f' Cloudflare after {MAX_RETRIES}'
                    f' retries (HTTP {resp.status_code})'
                )
                return None
            if resp.status_code != 200:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 3)
                    continue
                log.warning(
                    f'  [Proxy {proxy_label}] HTTP'
                    f' {resp.status_code} after'
                    f' {MAX_RETRIES} retries'
                )
                return None
            return body
        except Exception as e:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(attempt * 3)
            else:
                log.error(
                    f'  [Proxy {proxy_label}] Fetch failed'
                    f' after {MAX_RETRIES} retries: {e}'
                )
    return None


async def harvest_cf_cookies(
    context: BrowserContext,
    proxy: dict[str, str],
    harvest_url: str = '',
) -> dict[str, str]:
    """Solves Cloudflare via browser with warmup and harvests cookies.

    Mimics real browsing behaviour to build Cloudflare trust:
    homepage -> pause -> search page. Extracts cookies for reuse
    with curl_cffi.

    Args:
        context: Playwright browser context (proxy-configured).
        proxy: Proxy dict for logging.
        harvest_url: URL to navigate to for triggering Cloudflare.

    Returns:
        Dict of cookie name-value pairs.
    """
    if not harvest_url:
        harvest_url = SITE_URL + '/'
    page = await context.new_page()
    await Stealth().apply_stealth_async(page)
    try:
        # Step 1: Visit homepage first (warmup).
        await page.goto(
            SITE_URL + '/',
            wait_until='domcontentloaded',
        )
        await page.wait_for_timeout(random.randint(3000, 5000))
        await _wait_for_cf_challenge(page)

        # Step 2: Navigate to the actual search page.
        await page.goto(
            harvest_url,
            wait_until='domcontentloaded',
        )
        await page.wait_for_timeout(random.randint(8000, 12000))
        await _wait_for_cf_challenge(page)

        cookies = await context.cookies(SITE_URL)
        cookie_dict = {c['name']: c['value'] for c in cookies}
        log.info(
            f'  [Proxy {proxy["host"]}] Harvested '
            f'{len(cookie_dict)} cookies'
        )
        return cookie_dict
    except Exception as e:
        log.warning(
            f'  [Proxy {proxy["host"]}] Cookie harvest '
            f'failed: {e}'
        )
        return {}
    finally:
        await page.close()


async def setup_proxy_pool(
    browser: Browser,
    proxies: list[dict[str, str]],
    harvest_url: str,
) -> tuple[
    list[dict[str, str]],
    dict[str, dict[str, str]],
    asyncio.Event | None,
    asyncio.Task | None,
]:
    """Validates proxies, harvests Cloudflare cookies, and starts refresh.

    Loads proxies, enforces min/max limits, harvests Cloudflare cookies
    for each proxy via a real browser, drops proxies that didn't get
    enough cookies, and starts a background task to refresh cookies
    periodically.

    Args:
        browser: Playwright browser instance.
        proxies: List of proxy configuration dicts.
        harvest_url: URL to navigate to for triggering Cloudflare.

    Returns:
        A tuple of (active_proxies, cookie_store, stop_event,
        refresh_task). If no proxies survive validation, returns
        empty proxies with ``None`` for stop_event and refresh_task.
    """
    use_proxies = len(proxies) > 0

    if use_proxies and len(proxies) < MIN_PROXIES:
        log.warning(
            f'Found {len(proxies)} proxies in '
            f'proxies.txt, but at least {MIN_PROXIES} '
            f'are required. Falling back to browser.'
        )
        return [], {}, None, None
    if use_proxies and len(proxies) > MAX_PROXIES:
        log.warning(
            f'Found {len(proxies)} proxies in '
            f'proxies.txt, but the maximum is '
            f'{MAX_PROXIES}. Using the first '
            f'{MAX_PROXIES} only.'
        )
        proxies = proxies[:MAX_PROXIES]

    log.info(
        f'Using {len(proxies)} proxies '
        f'({WORKERS_PER_PROXY} workers/proxy)\n'
    )

    cookie_store: dict[str, dict[str, str]] = {}
    log.info('Harvesting Cloudflare cookies...')
    for proxy in proxies:
        try:
            proxy_ctx = await browser.new_context(
                proxy={
                    'server': (
                        f'http://{proxy["host"]}'
                        f':{proxy["port"]}'
                    ),
                    'username': proxy['username'],
                    'password': proxy['password'],
                },
                user_agent=proxy['user_agent'],
                viewport={'width': 1920, 'height': 1080},
            )
            cookies = await harvest_cf_cookies(
                proxy_ctx, proxy, harvest_url,
            )
            cookie_store[proxy['host']] = cookies
            await proxy_ctx.close()
        except Exception as e:
            log.warning(
                f'  [Proxy {proxy["host"]}] Setup '
                f'failed: {e}'
            )
            cookie_store[proxy['host']] = {}

    # Drop proxies that didn't get enough cookies
    # (likely failed Cloudflare challenge).
    before_count = len(proxies)
    proxies = [
        p for p in proxies
        if len(cookie_store.get(p['host'], {}))
        >= MIN_COOKIES
    ]
    if len(proxies) < before_count:
        dropped = before_count - len(proxies)
        log.warning(
            f'  Dropped {dropped} proxy/proxies with '
            f'fewer than {MIN_COOKIES} cookies'
        )
    if not proxies:
        log.warning(
            'No proxies passed cookie threshold; '
            'falling back to browser mode'
        )
        return [], cookie_store, None, None

    stop_event = asyncio.Event()
    refresh_task = asyncio.create_task(
        refresh_cookies_periodically(
            browser, proxies, cookie_store,
            stop_event, harvest_url,
        ),
    )
    return proxies, cookie_store, stop_event, refresh_task


async def refresh_cookies_periodically(
    browser: Browser,
    proxies: list[dict[str, str]],
    cookie_store: dict[str, dict[str, str]],
    stop_event: asyncio.Event,
    harvest_url: str = '',
) -> None:
    """Refreshes Cloudflare cookies at regular intervals.

    Runs in the background and updates the shared cookie store
    for all proxies every ``COOKIE_REFRESH_INTERVAL`` seconds.

    Args:
        browser: Playwright browser instance.
        proxies: List of proxy configuration dicts.
        cookie_store: Shared dict mapping proxy host to cookies.
        stop_event: Event to signal this task to stop.
        harvest_url: URL to navigate to for triggering Cloudflare.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=COOKIE_REFRESH_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            pass

        log.info('\n  [Cookie refresh] Refreshing cookies...')
        for proxy in proxies:
            if stop_event.is_set():
                break
            try:
                ctx = await browser.new_context(
                    proxy={
                        'server': f'http://{proxy["host"]}:{proxy["port"]}',
                        'username': proxy['username'],
                        'password': proxy['password'],
                    },
                    user_agent=proxy['user_agent'],
                    viewport={'width': 1920, 'height': 1080},
                )
                new_cookies = await harvest_cf_cookies(
                    ctx, proxy, harvest_url,
                )
                if new_cookies:
                    cookie_store[proxy['host']] = new_cookies
                await ctx.close()
            except Exception as e:
                log.warning(
                    f'  [Cookie refresh] Failed for'
                    f' {proxy["host"]}: {e}'
                )
        log.info('  [Cookie refresh] Done.')
