"""Configuration constants, column schemas, and logger for the scraper."""

import json
import logging
import os
import re

__version__ = '0.1.0'

# Output directories.
DIR_OUTPUT = 'output'
DIR_LOGS = 'logs'
DIR_DEBUG_HTML = os.path.join(DIR_LOGS, 'html_pages_for_debug')

# ── Config file loading ─────────────────────────────────────────────

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'config.json',
)


def _load_config() -> dict:
    """Loads search configuration from config.json.

    Returns the parsed config dict, or an empty dict if the file
    is missing or invalid (falling back to hardcoded defaults).
    """
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f'Warning: Could not load config.json: {e}')
        return {}


_config = _load_config()

# ── Location-specific settings (from config.json) ───────────────────

MIN_PRICE = _config.get('min_price', 100000)

EXCLUDED_LISTING_TYPES = _config.get('excluded_listing_types', [
    'land for sale',
    'plot for sale',
    'land to rent',
])

EXCLUDED_POSTCODE_PREFIXES = _config.get('excluded_postcode_prefixes', [])

PRICE_BREAKPOINTS = _config.get('price_breakpoints', [
    100000, 200000, 300000, 400000, 500000, 600000, 700000,
    800000, 900000, 1000000, 1250000, 1500000, 1750000,
    2000000, 2500000, 3000000, 3500000, 4000000, 5000000,
    6000000, 7000000, 8000000, 10000000, 15000000, '',
])

_LOCATION_NAME = _config.get('location_name', '')

SITE_URL = _config.get('site_url', '')
DETAIL_PAGE_URL_PREFIX = _config.get('detail_page_url_prefix', '')
IMAGE_CDN_DOMAIN = _config.get('image_cdn_domain', '')

# ── Scraper behaviour constants ─────────────────────────────────────

# Number of parallel tabs for detail page scraping.
NUM_TABS = 25

# Number of parallel tabs for Phase 1 ID collection.
NUM_TABS_PHASE1 = 10

# Number of properties to collect in test mode.
TEST_LIMIT = 50

# Maximum retries per property detail page before marking as failed.
MAX_RETRIES = 3

# Proxy configuration.
PROXY_FILE = 'proxies.txt'
MIN_PROXIES = 1
MAX_PROXIES = 20
WORKERS_PER_PROXY = 20
WORKERS_PER_PROXY_P1 = 5
REQUEST_DELAY_MIN = 0.5
REQUEST_DELAY_MAX = 1.0
COOKIE_REFRESH_INTERVAL = 300  # seconds (5 minutes)
MIN_COOKIES = 5  # proxies with fewer cookies after harvest are dropped
DEAD_PROXY_THRESHOLD = 3  # consecutive failures before marking a proxy dead
CF_CHALLENGE_TIMEOUT = 120000  # max ms to wait for Cloudflare challenge to resolve

# User-Agent / curl_cffi impersonate pairs. Each UA must match
# the TLS fingerprint curl_cffi sends for that impersonate string.
# Chrome-only to avoid UA/TLS mismatches that Cloudflare detects.
BROWSER_PROFILES = [
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'impersonate': 'chrome120',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'impersonate': 'chrome120',
    },
    {
        'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'impersonate': 'chrome120',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'impersonate': 'chrome123',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'impersonate': 'chrome123',
    },
    {
        'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'impersonate': 'chrome123',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'impersonate': 'chrome124',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'impersonate': 'chrome124',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'impersonate': 'chrome131',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'impersonate': 'chrome131',
    },
]

# ── Column schemas ──────────────────────────────────────────────────

# The 21 columns matching the database sales_data table.
COLUMNS = [
    'original_property_id',
    'property_id',
    'listing_name',
    'property_address',
    'postal_code',
    'source_url',
    'date_added',
    'property_description',
    'image_url',
    'num_beds',
    'num_baths',
    'broker_name',
    'broker_phone',
    'price',
    'square_footage',
    'council_tax_band',
    'features_json',
    'num_images',
    'all_image_urls_json',
    'tenure_type',
    'lease_years_remaining',
]

# Raw output columns, everything extractable from the HTML,
# no filtering, no cleaning, no defaults.
RAW_COLUMNS = [
    'property_id',
    'source_url',
    'listing_name',
    'property_address',
    'postal_code_raw',
    'price',
    'price_currency',
    'availability',
    'date_posted',
    'num_beds_raw',
    'num_baths_raw',
    'square_footage_raw',
    'tenure',
    'council_tax_band',
    'features_json',
    'property_description',
    'image_url_raw',
    'num_images',
    'all_image_urls_json',
    'broker_name',
    'broker_phone',
    'og_title',
    'meta_description',
    'jsonld_name',
    'jsonld_description',
    'jsonld_url',
    'additional_properties_json',
    'scrape_timestamp',
]

# ── Postcode pattern ────────────────────────────────────────────────

# Regex for matching full UK postcodes (outward + inward code).
_POSTCODE_PATTERN = re.compile(
    r'([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})', re.IGNORECASE,
)

# ── Logger ──────────────────────────────────────────────────────────

log = logging.getLogger('scraper')
