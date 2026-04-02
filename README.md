# UK Property Scraper

Scrapes UK property listings from search results into CSV files with a
configurable raw + clean data pipeline.

> **Disclaimer**
>
> This tool was built for personal use to research residential
> properties for sale in my country of residence. It is provided as-is
> for educational and personal reference purposes only.
>
> Users are solely responsible for ensuring their usage complies with
> applicable laws, including the UK General Data Protection Regulation
> (UK GDPR), the Data Protection Act 2018, and the terms of service of
> any website accessed. The author does not endorse, encourage, or
> accept liability for any use of this tool that violates applicable
> laws or third-party terms.
>
> No scraped data is included in this repository.

## Quick Start

### Windows

1. Install [Python 3.13+](https://www.python.org/downloads/)
   (tick **"Add Python to PATH"** during install)
2. Download or clone this repository
3. Copy `config.example.json` to `config.json` and edit it with your
   target search URL and settings (see [Configuration](#configuration))
4. Double-click `tools/run_scraper.bat`
5. Choose option **2 (Test)** for your first run
6. CSV files appear in the `output/` folder

### macOS / Linux

1. Install Python 3.13+ (`brew install python3` on macOS, or
   `sudo apt install python3 python3-venv python3-pip` on Ubuntu/Debian)
2. Download or clone this repository
3. Copy `config.example.json` to `config.json` and edit it with your
   target search URL and settings (see [Configuration](#configuration))
4. Run:
   ```bash
   chmod +x tools/run_scraper.sh
   ./tools/run_scraper.sh
   ```
5. Choose option **2 (Test)** for your first run
6. CSV files appear in the `output/` folder

### First run takes longer

The first run creates an isolated Python environment, installs
dependencies, and downloads a Chromium browser (~150 MB). Subsequent
runs start in seconds.

### CLI usage

```bash
# Full scrape
python scraper.py "<search_url>"

# Test mode (50 properties only)
python scraper.py --test "<search_url>"

# Skip Phase 1, resume from previously saved IDs
python scraper.py --ids-file output/ids_full_2026-03-30_14-22-05.txt

# Re-run Phase 3 (cleaning) only on a raw CSV, no browser needed
python scraper.py --clean-file output/raw_output_full.csv

# XOR-obfuscate property IDs in the clean output (any mode that runs Phase 3)
python scraper.py --clean-file output/raw_output_full.csv --xor-key 7997
python scraper.py --test --xor-key 7997 "<search_url>"

# Debug mode: fetch pages, save HTML, validate extraction
python scraper.py --debug "<search_url>"

# Show version
python scraper.py --version
```

## Configuration

### `config.json`

This file is **not included in the repository**; you must create it by
copying `config.example.json`. It contains all settings specific to your
target site and search area:

```json
{
    "site_url": "https://www.example.com",
    "detail_page_url_prefix": "https://www.example.com/for-sale/details/",
    "image_cdn_domain": "cdn.example.com",
    "search_url": "https://www.example.com/for-sale/property/your-area/",
    "location_name": "Your Area",
    "xor_key": 0,
    "min_price": 100000,
    "excluded_listing_types": ["land for sale", "plot for sale", "land to rent"],
    "excluded_postcode_prefixes": [],
    "price_breakpoints": [100000, 200000, 300000, "..."]
}
```

| Field | Description |
|---|---|
| `site_url` | Base URL of the target site (used for cookie harvesting and warmup) |
| `detail_page_url_prefix` | URL prefix for individual property pages (property ID is appended) |
| `image_cdn_domain` | CDN domain for image URL regex fallback (leave empty to disable) |
| `search_url` | The full URL of the search results page you want to scrape |
| `location_name` | Name of the area (used to clean addresses during postcode geocoding) |
| `xor_key` | Integer key for XOR obfuscation of property IDs (0 = disabled, overridden by `--xor-key` CLI flag) |
| `min_price` | Minimum property price to include in the clean output |
| `excluded_listing_types` | Listing title substrings to skip (case-insensitive) |
| `excluded_postcode_prefixes` | Postcode prefixes to exclude from the clean output |
| `price_breakpoints` | Price bands for segmenting large result sets (>1000 results). The last value should be an empty string to represent no maximum |

### Important notes about the scraper

This scraper was built for a specific UK property listing site and
relies on that site's HTML structure: JSON-LD structured data, specific
CSS class names, URL patterns, and pagination behaviour. **It will not
work on other sites without modification.** The code is published for
educational and reference purposes. If you want to adapt it for a
different site, you'll need to update the extraction logic in the
scraper to match that site's HTML structure.

### Proxy support

Both phases can use HTTP proxies with curl_cffi for dramatically faster
scraping. Instead of rendering each page in a full browser tab, the
scraper:

1. Solves Cloudflare once per proxy via a real browser (with homepage
   warmup) to harvest cookies
2. Drops proxies that didn't get enough cookies (likely failed the
   challenge)
3. Reuses cookies with lightweight curl_cffi requests that impersonate
   Chrome's TLS fingerprint (JA3/JA4)
4. Runs local browser tabs alongside proxies so your own IP contributes
5. Refreshes cookies every 5 minutes in the background
6. Falls back to a browser tab if a proxy is blocked after retries
7. Marks a proxy as dead after 3 consecutive failures and re-enqueues
   its work

Create a `proxies.txt` file in the project root with one proxy per line
(see `proxies.example.txt` for the format):

```
ip:port:username:password
```

If `proxies.txt` is missing or empty, the scraper uses browser tabs
only.

## Troubleshooting

| Problem | Solution |
|---|---|
| "Python is not installed" | Install Python 3.13+ from [python.org](https://www.python.org/downloads/), tick "Add to PATH", restart |
| "config.json not found" | Copy `config.example.json` to `config.json` and fill in your settings |
| Setup fails halfway | Delete the `venv` folder (or run `tools/reset.bat`) and try again |
| Chrome window closes immediately | Check if antivirus is blocking Chromium; add an exclusion |
| Out of memory | Close other applications. 4 GB+ free RAM recommended |
| "No property IDs found" | Run debug mode (option 3) and check the logs |

For a full environment check, run:

```bash
python tools/setup_check.py
```

## How It Works

The scraper runs in three phases:

**Phase 1: Collect property IDs** from search results. For large
result sets (>1000), automatically segments by price range and
paginates through each segment. Uses parallel browser tabs (or proxy
workers + local tabs when proxies are available).

**Phase 2: Scrape detail pages** for each property ID. Extracts
structured data from JSON-LD and DOM elements into a 28-column raw CSV.
Missing postcodes are estimated via Nominatim (OpenStreetMap) geocoding,
routed through proxies with per-IP rate limiting and result caching.

**Phase 3: Clean the raw output** into a 21-column CSV matching the
database schema. Applies all business rules: filters by listing type,
price, and postcode; fills defaults; normalises tenure and council tax
band; optionally XOR-obfuscates property IDs (via `--xor-key` or
`xor_key` in config); extracts square footage from descriptions as a
fallback; upgrades image resolution.

The raw CSV preserves everything from the HTML with no filtering. If
you want to change the cleaning logic (different price thresholds,
different column mappings, different XOR key), you can re-run Phase 3
without re-scraping using `--clean-file`.

## Output

Each run produces timestamped files:

- `output/ids_{mode}_{timestamp}.txt`, collected property IDs (reusable
  with the resume option)
- `output/raw_output_{mode}_{timestamp}.csv`, 28-column raw extraction
- `output/cleaned_output_{mode}_{timestamp}.csv`, 21-column clean output

### Raw output (28 columns)

| Column | Description |
|---|---|
| `property_id` | Listing ID |
| `source_url` | Listing URL |
| `listing_name` | Full listing title |
| `property_address` | Full address |
| `postal_code_raw` | Postcode (with Nominatim geocoding fallback) |
| `price` | Raw price string |
| `price_currency` | Currency code |
| `availability` | Availability status |
| `date_posted` | Date listed |
| `num_beds_raw` | Bedrooms as-is |
| `num_baths_raw` | Bathrooms as-is |
| `square_footage_raw` | Floor area from structured data |
| `tenure` | Freehold / Leasehold |
| `council_tax_band` | Council tax band |
| `features_json` | Features list as JSON array |
| `property_description` | Full description |
| `image_url_raw` | Primary image URL |
| `num_images` | Gallery image count |
| `all_image_urls_json` | All gallery URLs as JSON array |
| `broker_name` | Estate agent name |
| `broker_phone` | Estate agent phone |
| `og_title` | Open Graph title |
| `meta_description` | Meta description |
| `jsonld_name` | JSON-LD name field |
| `jsonld_description` | JSON-LD description field |
| `jsonld_url` | JSON-LD canonical URL |
| `additional_properties_json` | Full additional properties as JSON |
| `scrape_timestamp` | ISO timestamp of scrape |

### Clean output (21 columns)

| Column | Description |
|---|---|
| `original_property_id` | Original listing ID |
| `property_id` | XOR-obfuscated listing ID (if `xor_key` set, otherwise same as original) |
| `listing_name` | Listing title (truncated to 50 chars) |
| `property_address` | Address (truncated to 150 chars) |
| `postal_code` | Full postcode |
| `source_url` | Listing URL |
| `date_added` | Date listed (DD/MM/YYYY) |
| `property_description` | Description (truncated to 20,000 chars) |
| `image_url` | Primary image (upgraded to high resolution) |
| `num_beds` | Bedrooms (defaults to 1 if missing) |
| `num_baths` | Bathrooms (defaults to 1 if missing) |
| `broker_name` | Estate agent name (truncated to 75 chars) |
| `broker_phone` | Estate agent phone (truncated to 30 chars) |
| `price` | Price as integer |
| `square_footage` | Floor area (with description regex fallback) |
| `council_tax_band` | Council tax band A-H, or "Ask agent" if unknown |
| `features_json` | Features list as JSON array |
| `num_images` | Gallery image count |
| `all_image_urls_json` | All gallery URLs as JSON array |
| `tenure_type` | Freehold / Leasehold / Share of freehold / Ask agent |
| `lease_years_remaining` | Years remaining on lease (if applicable) |

## Technical Details

### Configuration constants

These are set in `src/config.py` and are not part of `config.json`
(they control scraper behaviour, not search-specific settings):

| Constant | Default | Description |
|---|---|---|
| `NUM_TABS` | 25 | Parallel browser tabs for Phase 2 |
| `NUM_TABS_PHASE1` | 10 | Parallel browser tabs for Phase 1 |
| `TEST_LIMIT` | 50 | Properties to collect in test mode |
| `MAX_RETRIES` | 3 | Retry attempts per detail page |
| `MIN_PROXIES` | 1 | Minimum proxies to enable proxy mode |
| `MAX_PROXIES` | 20 | Maximum proxies used |
| `WORKERS_PER_PROXY` | 20 | Concurrent workers per proxy (Phase 2) |
| `WORKERS_PER_PROXY_P1` | 5 | Concurrent workers per proxy (Phase 1) |
| `REQUEST_DELAY_MIN` | 0.5 | Min delay between proxy requests (seconds) |
| `REQUEST_DELAY_MAX` | 1.0 | Max delay between proxy requests (seconds) |
| `COOKIE_REFRESH_INTERVAL` | 300 | Seconds between cookie refreshes |
| `DEAD_PROXY_THRESHOLD` | 3 | Consecutive failures before marking proxy dead |
| `MIN_COOKIES` | 5 | Minimum cookies from harvest to keep a proxy |

### Performance

**Without proxies** (browser tabs only, 16GB+ RAM):
- Test mode (50 properties): ~2-3 minutes
- Full scrape (10,000+ properties): ~60+ minutes

**With proxies** (10 proxies, 20 workers each + local browser tabs):
- Full scrape including cookie harvest: ~5 minutes

### Logging

- **Console**: INFO and above
- **Log file**: DEBUG and above (full detail)
- Format: `2026-03-30 14:22:05 [INFO] Phase 1: collected 10861 unique IDs`
- Log files are saved to `logs/` and preserved even if the scraper crashes

## License

All rights reserved. This code is provided for educational and personal
reference purposes only. No permission is granted to copy, modify, or
distribute this software for profit.
