"""HTML parsing and data extraction from property listing pages."""

import json
import math
import re
from datetime import datetime

from bs4 import BeautifulSoup

from src.config import DETAIL_PAGE_URL_PREFIX
from src.config import IMAGE_CDN_DOMAIN
from src.config import _POSTCODE_PATTERN
from src.config import log
from src.geocode import format_postcode
from src.geocode import lookup_postcode_from_address


def extract_listing_ids(html_content: str) -> list[str]:
    """Extracts property IDs from a search results page.

    Args:
        html_content: Raw HTML of a search results page.

    Returns:
        Deduplicated list of property ID strings, preserving order.
    """
    ids = re.findall(r'/details/([0-9]+)/', html_content)
    return list(dict.fromkeys(ids))


def extract_result_count(html_content: str) -> int:
    """Extracts the total result count from a search results page.

    Args:
        html_content: Raw HTML of a search results page.

    Returns:
        Total number of results, or 0 if not found.
    """
    match = re.search(r'>([0-9]+) results</p>', html_content)
    return int(match.group(1)) if match else 0


def generate_pagination_urls(
    total_results: int,
    base_url: str,
) -> list[str]:
    """Generates paginated URLs for pages 2 onwards.

    Args:
        total_results: Total number of search results.
        base_url: The base search URL (page 1).

    Returns:
        List of URLs for pages 2 through ceil(total/25).
    """
    total_pages = math.ceil(total_results / 25)
    connector = '&' if '?' in base_url else '?'
    return [
        f'{base_url}{connector}pn={page_num}'
        for page_num in range(2, total_pages + 1)
    ]


def extract_jsonld(
    html_content: str,
) -> tuple[dict | None, BeautifulSoup]:
    """Extracts the RealEstateListing JSON-LD block from page HTML.

    Args:
        html_content: Raw HTML of a property detail page.

    Returns:
        A tuple of (JSON-LD dict or None, BeautifulSoup object).
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    for script_tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script_tag.string)
            if (isinstance(data, dict)
                    and data.get('@type') == 'RealEstateListing'):
                return data, soup
        except (json.JSONDecodeError, TypeError):
            continue
    return None, soup


def _extract_postal_code_raw(soup: BeautifulSoup) -> str:
    """Extracts a postcode from DOM only, no geocoding fallback.

    Searches the address tag, og:title, and meta description for a
    full UK postcode. Returns empty string if none found rather than
    calling Nominatim.

    Args:
        soup: Parsed HTML of the property detail page.

    Returns:
        Formatted postcode string, or empty string if not found.
    """
    address = _extract_address(soup)
    pc_match = _POSTCODE_PATTERN.search(address)

    if not pc_match:
        og_title = soup.find('meta', property='og:title')
        if og_title:
            pc_match = _POSTCODE_PATTERN.search(
                og_title.get('content', ''),
            )

    if not pc_match:
        meta_desc = soup.find('meta', {'name': 'description'})
        if meta_desc:
            pc_match = _POSTCODE_PATTERN.search(
                meta_desc.get('content', ''),
            )

    if pc_match:
        return format_postcode(pc_match.group(1))
    return ''


def _extract_features_raw(
    jsonld: dict,
) -> tuple[str, str, str]:
    """Extracts beds, baths, and square footage without defaults.

    Returns raw values as-is; missing or zero values are not
    defaulted to ``'1'`` (that happens in the clean phase).

    Args:
        jsonld: Parsed JSON-LD dict.

    Returns:
        Tuple of (num_beds, num_baths, square_footage) as raw strings.
    """
    num_beds = ''
    num_baths = ''
    square_footage = ''

    for prop in jsonld.get('additionalProperty', []):
        name = prop.get('name', '')
        val = prop.get('value', '')
        if name == 'Bedrooms' and val:
            num_beds = str(val)
        elif name == 'Bathrooms' and val:
            num_baths = str(val)
        elif name in ('Floor size', 'Floor area') and val:
            square_footage = str(val)

    return num_beds, num_baths, square_footage


def _extract_property_info(
    soup: BeautifulSoup,
) -> dict[str, str]:
    """Extracts structured property info (tenure, council tax, etc.).

    Looks for the NtsInfo section containing labelled key-value pairs.

    Args:
        soup: Parsed HTML of the property detail page.

    Returns:
        Dict mapping lowercased labels to values (e.g.
        ``{'tenure': 'Freehold', 'council tax band': 'D'}``).
    """
    info: dict[str, str] = {}
    titles = soup.select('[class*="ntsInfoItemTitle"]')
    for title in titles:
        label = title.get_text(strip=True)
        parent = title.parent
        value_el = (
            parent.select_one('[class*="ntsInfoItemTextWrapper"]')
            if parent else None
        )
        value = value_el.get_text(strip=True) if value_el else ''
        if label:
            info[label.lower()] = value
    return info


def _extract_features_list(soup: BeautifulSoup) -> list[str]:
    """Extracts the bullet-point features list from the page.

    Args:
        soup: Parsed HTML of the property detail page.

    Returns:
        List of feature strings.
    """
    features = []
    items = soup.select('[class*="featuresList"] li')
    for item in items:
        text = item.get_text(strip=True)
        if text:
            features.append(text)
    return features


def _extract_gallery_images(
    soup: BeautifulSoup,
) -> list[str]:
    """Extracts all gallery image URLs from the page.

    Looks for ``<source>`` tags inside gallery slides and collects
    the highest-resolution JPEG URLs (typically 1024x768). Falls
    back to regex extraction if gallery slides are not found.

    Args:
        soup: Parsed HTML of the property detail page.

    Returns:
        Deduplicated list of image URLs.
    """
    urls: list[str] = []
    slides = soup.select('[data-key^="gallery-slide"]')
    for slide in slides:
        # Prefer the high-res JPEG source.
        sources = slide.find_all('source', type='image/jpeg')
        for source in sources:
            srcset = source.get('srcset', '')
            if '/1024/768/' in srcset:
                url = srcset.split()[0]
                if url not in urls:
                    urls.append(url)
                break
        else:
            # Fall back to any JPEG srcset.
            for source in sources:
                srcset = source.get('srcset', '')
                parts = srcset.split()
                if not parts:
                    continue
                url = parts[0]
                if url not in urls:
                    urls.append(url)
                    break

    # If no slides found, try regex on the full HTML.
    if not urls and IMAGE_CDN_DOMAIN:
        escaped = re.escape(IMAGE_CDN_DOMAIN)
        found = re.findall(
            rf'https://{escaped}/u/\d+/\d+/[a-f0-9]+\.jpg',
            str(soup),
        )
        seen: set[str] = set()
        for u in found:
            # Normalise to highest resolution found.
            key = u.split('/')[-1]
            if key not in seen:
                seen.add(key)
                urls.append(u)

    return urls


def extract_raw_property_data(
    html_content: str,
    property_id: str,
    proxy_url: str | None = None,
) -> dict[str, str] | None:
    """Extracts all available data from a property detail page.

    Applies no filtering (price, listing type, postcode), no
    defaults, and no truncation. Falls back to geocoding via
    Nominatim when no postcode is found in the HTML, routing
    the request through ``proxy_url`` if provided. Captures
    everything the page provides for the raw output CSV.

    Args:
        html_content: Raw HTML of a property detail page.
        property_id: The property ID.
        proxy_url: Optional HTTP proxy URL for Nominatim geocoding.

    Returns:
        A dict of raw column values, or ``None`` on parse failure.
    """
    try:
        jsonld, soup = extract_jsonld(html_content)
        if not jsonld:
            return None

        listing_name = _extract_listing_name(soup)
        address = _extract_address(soup)
        postal_code = _extract_postal_code_raw(soup)
        if not postal_code and address:
            postal_code = lookup_postcode_from_address(
                address, proxy_url=proxy_url,
            )
        num_beds, num_baths, sqft = _extract_features_raw(jsonld)
        description = _extract_description(soup)
        broker_name, broker_phone = _extract_broker_info(soup)

        # Offers data.
        offers = jsonld.get('offers', {})
        raw_price = str(offers.get('price', ''))
        price_currency = str(offers.get('priceCurrency', ''))
        availability = str(offers.get('availability', ''))

        # Structured property info (tenure, council tax, etc.).
        prop_info = _extract_property_info(soup)
        tenure = prop_info.get('tenure', '')
        council_tax_band = prop_info.get('council tax band', '')

        # Features list.
        features = _extract_features_list(soup)
        features_json = (
            json.dumps(features, ensure_ascii=False)
            if features else ''
        )

        # Gallery images.
        gallery_urls = _extract_gallery_images(soup)
        num_images = str(len(gallery_urls)) if gallery_urls else ''
        all_images_json = (
            json.dumps(gallery_urls, ensure_ascii=False)
            if gallery_urls else ''
        )

        # Meta tags.
        og_title_tag = soup.find('meta', property='og:title')
        og_title = (
            og_title_tag.get('content', '') if og_title_tag else ''
        )
        meta_desc_tag = soup.find('meta', {'name': 'description'})
        meta_description = (
            meta_desc_tag.get('content', '')
            if meta_desc_tag else ''
        )

        # Full additionalProperty array as JSON string.
        additional_props = jsonld.get('additionalProperty', [])
        additional_json = json.dumps(
            additional_props, ensure_ascii=False,
        ) if additional_props else ''

        return {
            'property_id': property_id,
            'source_url': (
                f'{DETAIL_PAGE_URL_PREFIX}'
                f'{property_id}/'
            ),
            'listing_name': listing_name,
            'property_address': address,
            'postal_code_raw': postal_code,
            'price': raw_price,
            'price_currency': price_currency,
            'availability': availability,
            'date_posted': jsonld.get('datePosted', '') or '',
            'num_beds_raw': num_beds,
            'num_baths_raw': num_baths,
            'square_footage_raw': sqft,
            'tenure': tenure,
            'council_tax_band': council_tax_band,
            'features_json': features_json,
            'property_description': description,
            'image_url_raw': jsonld.get('image', '') or '',
            'num_images': num_images,
            'all_image_urls_json': all_images_json,
            'broker_name': broker_name,
            'broker_phone': broker_phone,
            'og_title': og_title,
            'meta_description': meta_description,
            'jsonld_name': jsonld.get('name', '') or '',
            'jsonld_description': (
                jsonld.get('description', '') or ''
            ),
            'jsonld_url': jsonld.get('url', '') or '',
            'additional_properties_json': additional_json,
            'scrape_timestamp': datetime.now().isoformat(),
        }
    except Exception as e:
        log.error(f'Error parsing raw data for {property_id}: {e}')
        return None


def _extract_listing_name(soup: BeautifulSoup) -> str:
    """Extracts the listing name from the page h1 tag."""
    h1 = soup.find('h1')
    if not h1:
        return ''
    name = h1.get_text(separator=' ', strip=True)
    address_tag = h1.find('address')
    if address_tag:
        name = name.replace(
            address_tag.get_text(strip=True), '',
        ).strip()
    return name


def _extract_address(soup: BeautifulSoup) -> str:
    """Extracts the property address from the address tag."""
    tag = soup.find('address')
    return tag.get_text(strip=True) if tag else ''


def _extract_description(soup: BeautifulSoup) -> str:
    """Extracts the property description from the detail page."""
    desc_div = soup.find(id='detailed-desc')
    if not desc_div:
        return ''
    text = desc_div.get_text(separator=' ', strip=True)
    return re.sub(r'\s+', ' ', text).strip()


def _extract_broker_info(soup: BeautifulSoup) -> tuple[str, str]:
    """Extracts broker name and phone number from the contact section.

    Returns:
        Tuple of (broker_name, broker_phone).
    """
    broker_name = ''
    broker_p = soup.select_one('p[class*="contactTitle"]')
    if broker_p:
        broker_name = broker_p.get_text(strip=True)
    else:
        broker_img = soup.select_one('img[class*="contactLogo"]')
        if broker_img:
            alt = broker_img.get('alt', '')
            m = re.match(r'Logo of (.+)', alt)
            if m:
                broker_name = m.group(1)

    broker_phone = ''
    contact_section = (
        soup.select_one('div[class*="contact"]')
        or soup.select_one('div[class*="Contact"]')
    )
    if contact_section:
        phone_matches = re.findall(
            r'(?:0\d{4}\s?\d{6,7}|\(\d{4,5}\)\s?\d{6,7})',
            contact_section.get_text(),
        )
        if phone_matches:
            broker_phone = phone_matches[0]

    return broker_name, broker_phone
