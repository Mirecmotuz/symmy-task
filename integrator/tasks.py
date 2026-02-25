import logging

from celery import shared_task
from django.conf import settings

from .eshop_client import EshopClient
from .models import ProductSyncState
from .transformer import compute_hash, load_and_transform

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='integrator.sync_products')
def sync_products_task(self):
    """
    Synchronise ERP products with the eshop API.

    Steps:
      1. Load and transform products from erp_data.json.
      2. For each valid product compute a SHA-256 content hash.
      3. Compare with the last known hash stored in ProductSyncState.
      4. Send only changed (or new) products to the eshop API.
      5. Persist the new hash so the next run can skip unchanged products.
    """
    logger.info("Starting ERP → eshop sync task.")

    products = load_and_transform(settings.ERP_DATA_PATH)
    logger.info("Loaded %d valid products from ERP data.", len(products))

    client = EshopClient()
    sent = skipped = errors = 0

    for product in products:
        sku = product['sku']
        new_hash = compute_hash(product)

        try:
            try:
                state = ProductSyncState.objects.get(sku=sku)
                is_new = False
            except ProductSyncState.DoesNotExist:
                state = None
                is_new = True

            if state is not None and state.content_hash == new_hash:
                logger.debug("SKU %s unchanged – skipping.", sku)
                skipped += 1
                continue

            client.send_product(product, is_new=is_new)

            if state is None:
                state = ProductSyncState(sku=sku)
            state.content_hash = new_hash
            state.synced_as_new = is_new
            state.save()
            sent += 1
            logger.info("SKU %s %s successfully.", sku, 'created' if is_new else 'updated')

        except Exception as exc:
            errors += 1
            logger.error("Failed to sync SKU %s: %s", sku, exc)

    logger.info(
        "Sync complete. sent=%d, skipped=%d, errors=%d.",
        sent, skipped, errors,
    )
    return {'sent': sent, 'skipped': skipped, 'errors': errors}
