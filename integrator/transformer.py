import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VAT_RATE = 0.21


def load_erp_data(path) -> list[dict]:
    """Load ERP JSON from disk and deduplicate by SKU (first occurrence wins)."""
    with open(path, encoding='utf-8') as f:
        raw_list = json.load(f)

    seen_skus = {}
    for item in raw_list:
        sku = item.get('id')
        if sku in seen_skus:
            logger.warning("Duplicate SKU %s – keeping first occurrence, skipping duplicate.", sku)
        else:
            seen_skus[sku] = item

    return list(seen_skus.values())


def _parse_stock_value(value) -> int:
    """Convert a stock value to int; non-numeric values count as 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Non-numeric stock value %r – treating as 0.", value)
        return 0


def transform_product(raw: dict) -> Optional[dict]:
    """
    Transform a raw ERP product dict into the eshop API payload.

    Returns None (and logs a warning) if the product has an invalid price.
    """
    sku = raw.get('id', '<unknown>')

    price_excl = raw.get('price_vat_excl')
    if price_excl is None:
        logger.warning("Skipping SKU %s – price is null.", sku)
        return None
    if price_excl < 0:
        logger.warning("Skipping SKU %s – negative price: %s.", sku, price_excl)
        return None

    stocks = raw.get('stocks') or {}
    total_stock = sum(_parse_stock_value(v) for v in stocks.values())

    attributes = raw.get('attributes') or {}
    color = attributes.get('color') or 'N/A'

    return {
        'sku': sku,
        'title': raw.get('title', ''),
        'price': round(price_excl * (1 + VAT_RATE), 2),
        'stock': total_stock,
        'color': color,
    }


def compute_hash(product: dict) -> str:
    """Compute a stable SHA-256 hash of the product dict for delta sync."""
    serialized = json.dumps(product, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def load_and_transform(path) -> list[dict]:
    """Convenience: load ERP data and return only valid transformed products."""
    raw_products = load_erp_data(path)
    result = []
    for raw in raw_products:
        transformed = transform_product(raw)
        if transformed is not None:
            result.append(transformed)
    return result
