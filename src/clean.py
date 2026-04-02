"""Phase 3: clean raw CSV output into database-ready format."""

import polars as pl

from src.config import EXCLUDED_LISTING_TYPES
from src.config import EXCLUDED_POSTCODE_PREFIXES
from src.config import MIN_PRICE
from src.config import log


def clean_output(
    raw_csv_path: str,
    clean_csv_path: str,
    xor_key: int = 0,
) -> int:
    """Transforms the raw CSV into a cleaned CSV matching the DB schema.

    Reads the raw output, applies all filtering (listing type, price,
    postcode, image), normalises tenure and council tax band, joins
    additional columns from raw, and writes the 21-column clean CSV.

    Args:
        raw_csv_path: Path to the raw CSV produced during scraping.
        clean_csv_path: Path to write the cleaned CSV.
        xor_key: Integer key for XOR obfuscation of property IDs.
            If 0, no obfuscation is applied.

    Returns:
        Number of rows in the clean output.
    """
    log.info('--- Phase 3: Cleaning raw output ---')
    df = pl.read_csv(raw_csv_path, infer_schema_length=0)
    log.info(f'  Raw rows: {len(df)}')

    # 1. Exclude land/plots.
    for exc in EXCLUDED_LISTING_TYPES:
        df = df.filter(
            ~pl.col('listing_name').str.to_lowercase().str.contains(
                exc, literal=True,
            ),
        )
    log.info(
        f'  After excluding land/plots: {len(df)}',
    )

    # 2. Parse price and filter below MIN_PRICE.
    df = df.with_columns(
        pl.col('price')
        .str.replace_all(',', '')
        .cast(pl.Float64, strict=False)
        .cast(pl.Int64, strict=False)
        .fill_null(0)
        .alias('price_int'),
    )
    df = df.filter(pl.col('price_int') >= MIN_PRICE)
    log.info(
        f'  After excluding below \u00a3{MIN_PRICE:,}: {len(df)}',
    )

    # 3. Use postcodes from Phase 2 and drop rows with empty postal_code.
    df = df.with_columns(
        pl.col('postal_code_raw').fill_null('').alias('postal_code'),
    )
    before_pc = len(df)
    df = df.filter(pl.col('postal_code') != '')
    dropped_pc = before_pc - len(df)
    log.info(
        f'  After dropping empty postcodes: {len(df)}'
        f' (dropped {dropped_pc})',
    )

    # 4. Exclude Greater London postcodes.
    _pc_stripped = (
        pl.col('postal_code')
        .str.to_uppercase()
        .str.replace_all(' ', '')
    )
    _pc_filter = pl.lit(False)
    for prefix in EXCLUDED_POSTCODE_PREFIXES:
        _pc_filter = _pc_filter | _pc_stripped.str.starts_with(prefix)
    df = df.filter(~_pc_filter)
    log.info(
        f'  After excluding Greater London postcodes: {len(df)}',
    )

    # 5. Default beds/baths: empty or '0' becomes '1'.
    df = df.with_columns(
        pl.when(
            (pl.col('num_beds_raw') == '')
            | (pl.col('num_beds_raw') == '0')
            | pl.col('num_beds_raw').is_null(),
        )
        .then(pl.lit('1'))
        .otherwise(pl.col('num_beds_raw'))
        .alias('num_beds'),
        pl.when(
            (pl.col('num_baths_raw') == '')
            | (pl.col('num_baths_raw') == '0')
            | pl.col('num_baths_raw').is_null(),
        )
        .then(pl.lit('1'))
        .otherwise(pl.col('num_baths_raw'))
        .alias('num_baths'),
    )

    # 6. Square footage: use raw value, fall back to description extraction.
    _sqft_from_desc = (
        pl.col('property_description')
        .fill_null('')
        .str.extract(r'([\d,]+)\s*sq\.?\s*ft', 1)
        .str.replace_all(',', '')
    )
    _sqft_raw = pl.col('square_footage_raw').fill_null('')
    df = df.with_columns(
        pl.when(_sqft_raw != '')
        .then(_sqft_raw)
        .otherwise(_sqft_from_desc)
        .fill_null('')
        .alias('square_footage'),
    )

    # 7. Upgrade image URL resolution and drop rows with null image_url.
    df = df.with_columns(
        pl.col('image_url_raw')
        .str.replace(r'/u/\d+/\d+/', '/u/1600/1200/')
        .alias('image_url'),
    )
    before_img = len(df)
    df = df.filter(
        pl.col('image_url').is_not_null()
        & (pl.col('image_url') != ''),
    )
    dropped_img = before_img - len(df)
    if dropped_img > 0:
        log.info(
            f'  After dropping null image_url: {len(df)}'
            f' (dropped {dropped_img})',
        )

    # 8. Normalise tenure into tenure_type + lease_years_remaining.
    _tenure = pl.col('tenure').fill_null('').str.strip_chars()
    df = df.with_columns(
        pl.when(_tenure == 'Freehold')
        .then(pl.lit('Freehold'))
        .when(_tenure.str.starts_with('Leasehold'))
        .then(pl.lit('Leasehold'))
        .when(_tenure == 'Share of freehold')
        .then(pl.lit('Share of freehold'))
        .otherwise(pl.lit('Ask agent'))
        .alias('tenure_type'),
        _tenure
        .str.extract(r'\((\d+)\s+years?\)', 1)
        .alias('lease_years_remaining'),
    )

    # 9. Standardise council_tax_band.
    _valid_bands = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    _ct = pl.col('council_tax_band').fill_null('').str.strip_chars()
    df = df.with_columns(
        pl.when(_ct.is_in(_valid_bands))
        .then(_ct)
        .otherwise(pl.lit('Ask agent'))
        .alias('council_tax_band'),
    )

    # 10. Obfuscate property ID with XOR.
    if xor_key:
        df = df.with_columns(
            pl.col('property_id').alias('original_property_id'),
            pl.col('property_id')
            .cast(pl.Int64)
            .xor(xor_key)
            .cast(pl.Utf8)
            .alias('property_id'),
        )
    else:
        df = df.with_columns(
            pl.col('property_id').alias('original_property_id'),
        )

    # 11. Truncate and format fields.
    df = df.with_columns(
        pl.col('listing_name').fill_null('').str.slice(0, 50),
        pl.col('property_address').fill_null('').str.slice(0, 150),
        pl.col('date_posted').fill_null('').str.slice(0, 10)
        .str.to_date('%Y-%m-%d', strict=False)
        .dt.strftime('%d/%m/%Y')
        .fill_null('')
        .alias('date_added'),
        pl.col('property_description').fill_null('')
        .str.slice(0, 20000),
        pl.col('broker_name').fill_null('').str.slice(0, 75),
        pl.col('broker_phone').fill_null('').str.slice(0, 30),
        pl.col('square_footage').fill_null('').str.slice(0, 20),
        pl.col('features_json').fill_null(''),
        pl.col('all_image_urls_json').fill_null(''),
        pl.col('num_images').fill_null('0'),
    )

    # 12. Select final 21 columns and write.
    df = df.select([
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
        pl.col('price_int').cast(pl.Utf8).alias('price'),
        'square_footage',
        'council_tax_band',
        'features_json',
        'num_images',
        'all_image_urls_json',
        'tenure_type',
        'lease_years_remaining',
    ])

    df.write_csv(clean_csv_path)
    log.info(f'  Clean rows: {len(df)}')
    log.info(f'  Clean output saved to {clean_csv_path}')
    return len(df)
