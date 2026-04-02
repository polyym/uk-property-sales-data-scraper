"""Nominatim geocoding with per-IP rate limiting and caching."""

import json
import re
import threading
import time
import urllib.parse
import urllib.request

from src.config import _LOCATION_NAME
from src.config import log

# Nominatim rate limiting (1 req/sec per IP) and geocode caching.
# Uses threading.Lock because scrape_with_proxy runs extraction
# in real threads via asyncio.to_thread.
_nominatim_rate_lock = threading.Lock()
_nominatim_last_call: dict[str, float] = {}
_geocode_cache_lock = threading.Lock()
_geocode_cache: dict[str, str] = {}


def format_postcode(raw_postcode: str) -> str:
    """Formats a UK postcode to standard form with a space.

    The inward code is always the last 3 characters, so the space is
    inserted before them (e.g. ``GU32JR`` becomes ``GU3 2JR``).

    Args:
        raw_postcode: Raw postcode string, possibly missing the space.

    Returns:
        Formatted postcode, or the original stripped/uppercased if it
        is too short to split.
    """
    pc = raw_postcode.strip().upper().replace(' ', '')
    if len(pc) >= 5:
        return pc[:-3] + ' ' + pc[-3:]
    return raw_postcode.strip().upper()


def lookup_postcode_from_address(
    address: str,
    proxy_url: str | None = None,
) -> str:
    """Estimates a full UK postcode from a street address.

    Strips the partial postcode from the address and geocodes via
    Nominatim for a street-level postcode. Results (including empty
    strings for failed lookups) are cached to avoid redundant API
    calls for properties on the same street.

    Args:
        address: Property address string, possibly ending with a
            partial postcode.
        proxy_url: Optional HTTP proxy URL to route the Nominatim
            request through.

    Returns:
        Formatted postcode string, or empty string if lookup fails.
    """
    clean_address = re.sub(
        r'\s*[A-Z]{1,2}\d[A-Z\d]?\s*$', '', address.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if _LOCATION_NAME:
        clean_address = re.sub(
            rf',?\s*{re.escape(_LOCATION_NAME)}\s*$', '',
            clean_address, flags=re.IGNORECASE,
        ).strip()

    if not clean_address:
        return ''

    # Check cache (includes negative results).
    with _geocode_cache_lock:
        if clean_address in _geocode_cache:
            return _geocode_cache[clean_address]

    postcode = _geocode_nominatim(
        clean_address, proxy_url=proxy_url,
    )

    # Cache the result (empty string = negative cache).
    with _geocode_cache_lock:
        _geocode_cache[clean_address] = postcode

    return postcode


def _geocode_nominatim(
    address: str,
    proxy_url: str | None = None,
) -> str:
    """Geocodes an address via Nominatim to extract a postcode.

    Enforces Nominatim's usage policy of max 1 request per second
    per IP. When ``proxy_url`` is provided, the request is routed
    through that proxy (giving each proxy its own rate-limit bucket).

    Args:
        address: Cleaned street address (no partial postcode).
        proxy_url: Optional HTTP proxy URL to route the request
            through (e.g. ``http://user:pass@host:port``).

    Returns:
        Formatted postcode, or empty string on failure.
    """
    # Determine rate-limit key (one bucket per IP).
    if proxy_url:
        parsed = urllib.parse.urlparse(proxy_url)
        rate_key = parsed.hostname or 'local'
    else:
        rate_key = 'local'

    # Enforce 1 req/sec per IP.
    with _nominatim_rate_lock:
        last = _nominatim_last_call.get(rate_key, 0.0)
        wait = 1.0 - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _nominatim_last_call[rate_key] = time.monotonic()

    try:
        query = urllib.parse.urlencode({
            'q': address,
            'countrycodes': 'gb',
            'format': 'jsonv2',
            'addressdetails': 1,
            'limit': 1,
        })
        url = f'https://nominatim.openstreetmap.org/search?{query}'
        req = urllib.request.Request(
            url, headers={'User-Agent': 'PropertyScraper/1.0'},
        )

        if proxy_url:
            handler = urllib.request.ProxyHandler({
                'http': proxy_url,
                'https': proxy_url,
            })
            opener = urllib.request.build_opener(handler)
            response = opener.open(req, timeout=10)
        else:
            response = urllib.request.urlopen(req, timeout=10)

        with response:
            data = json.loads(response.read().decode())
            if data and 'address' in data[0]:
                postcode = data[0]['address'].get('postcode', '')
                if postcode:
                    return format_postcode(postcode)
    except Exception as e:
        log.debug(f'Nominatim geocode failed: {e}')
    return ''
