import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import responses as responses_lib

from integrator.models import ProductSyncState
from integrator.tasks import sync_products_task
from integrator.transformer import compute_hash

BASE_URL = 'https://api.fake-eshop.cz/v1'

VALID_ERP_DATA = [
    {
        'id': 'SKU-001',
        'title': 'Kávovar Espresso',
        'price_vat_excl': 100.0,
        'stocks': {'praha': 5, 'brno': 3},
        'attributes': {'color': 'stříbrná'},
    },
    {
        'id': 'SKU-003',
        'title': 'Mlýnek',
        'price_vat_excl': 50.0,
        'stocks': {'praha': 2},
        'attributes': None,
    },
]

INVALID_ERP_DATA = [
    {'id': 'SKU-BAD-NULL', 'title': 'Bad', 'price_vat_excl': None, 'stocks': {}, 'attributes': {}},
    {'id': 'SKU-BAD-NEG', 'title': 'Bad neg', 'price_vat_excl': -1, 'stocks': {}, 'attributes': {}},
]


@pytest.fixture()
def erp_file(tmp_path):
    """Write ERP data to a temp file and return its path."""
    def _make(data):
        p = tmp_path / 'erp_data.json'
        p.write_text(json.dumps(data), encoding='utf-8')
        return p
    return _make



@pytest.fixture(autouse=True)
def override_settings(settings):
    settings.ESHOP_API_BASE_URL = BASE_URL
    settings.ESHOP_API_KEY = 'symma-secret-token'

# ---------------------------------------------------------------------------
# New product → POST + ProductSyncState created
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_new_product_sends_post_and_creates_sync_state(erp_file, settings):
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA)
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)

    result = sync_products_task()

    assert result['sent'] == 2
    assert result['skipped'] == 0
    assert ProductSyncState.objects.count() == 2
    methods = [c.request.method for c in responses_lib.calls]
    assert all(m == 'POST' for m in methods)


# ---------------------------------------------------------------------------
# Unchanged product → no API call (delta sync)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_unchanged_product_is_not_sent(erp_file, settings):
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])

    transformed = {
        'sku': 'SKU-001',
        'title': 'Kávovar Espresso',
        'price': round(100.0 * 1.21, 2),
        'stock': 8,
        'color': 'stříbrná',
    }
    ProductSyncState.objects.create(
        sku='SKU-001',
        content_hash=compute_hash(transformed),
        synced_as_new=False,
    )

    result = sync_products_task()

    assert result['sent'] == 0
    assert result['skipped'] == 1
    assert len(responses_lib.calls) == 0


# ---------------------------------------------------------------------------
# Changed product → PATCH
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_changed_product_sends_patch(erp_file, settings):
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])

    ProductSyncState.objects.create(
        sku='SKU-001',
        content_hash='old-hash-that-does-not-match',
        synced_as_new=False,
    )
    responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/SKU-001/', json={}, status=200)

    result = sync_products_task()

    assert result['sent'] == 1
    assert responses_lib.calls[0].request.method == 'PATCH'


# ---------------------------------------------------------------------------
# Invalid products are skipped, task completes normally
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_invalid_products_skipped_task_completes(erp_file, settings):
    mixed = VALID_ERP_DATA[:1] + INVALID_ERP_DATA
    settings.ERP_DATA_PATH = erp_file(mixed)
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)

    result = sync_products_task()

    assert result['sent'] == 1
    assert result['errors'] == 0
    assert ProductSyncState.objects.count() == 1


# ---------------------------------------------------------------------------
# API error does not abort entire task
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_api_error_does_not_abort_remaining_products(erp_file, settings):
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA)
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', status=500)
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)

    result = sync_products_task()

    assert result['errors'] == 1
    assert result['sent'] == 1


# ---------------------------------------------------------------------------
# (1) Hash je uložený so správnou hodnotou po úspešnom syncu
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_correct_hash_persisted_after_post(erp_file, settings):
    """Po úspešnom POST musí content_hash v DB zodpovedať skutočnému hashu produktu."""
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)

    sync_products_task()

    expected_product = {
        'sku': 'SKU-001',
        'title': 'Kávovar Espresso',
        'price': round(100.0 * 1.21, 2),
        'stock': 8,
        'color': 'stříbrná',
    }
    state = ProductSyncState.objects.get(sku='SKU-001')
    assert state.content_hash == compute_hash(expected_product)


@pytest.mark.django_db
@responses_lib.activate
def test_correct_hash_persisted_after_patch(erp_file, settings):
    """Po úspešnom PATCH musí content_hash v DB zodpovedať novému hashu, nie starému."""
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])
    ProductSyncState.objects.create(
        sku='SKU-001',
        content_hash='old-hash-that-does-not-match',
        synced_as_new=False,
    )
    responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/SKU-001/', json={}, status=200)

    sync_products_task()

    expected_product = {
        'sku': 'SKU-001',
        'title': 'Kávovar Espresso',
        'price': round(100.0 * 1.21, 2),
        'stock': 8,
        'color': 'stříbrná',
    }
    state = ProductSyncState.objects.get(sku='SKU-001')
    assert state.content_hash == compute_hash(expected_product)


# ---------------------------------------------------------------------------
# (2) synced_as_new je správne nastavené po syncu
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_synced_as_new_is_true_for_new_product(erp_file, settings):
    """Nový produkt (POST) musí mať synced_as_new=True."""
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)

    sync_products_task()

    state = ProductSyncState.objects.get(sku='SKU-001')
    assert state.synced_as_new is True


@pytest.mark.django_db
@responses_lib.activate
def test_synced_as_new_is_false_for_updated_product(erp_file, settings):
    """Aktualizovaný produkt (PATCH) musí mať synced_as_new=False."""
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])
    ProductSyncState.objects.create(
        sku='SKU-001',
        content_hash='old-hash-that-does-not-match',
        synced_as_new=True,
    )
    responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/SKU-001/', json={}, status=200)

    sync_products_task()

    state = ProductSyncState.objects.get(sku='SKU-001')
    assert state.synced_as_new is False


# ---------------------------------------------------------------------------
# (3) Po API chybe sa stav v DB NEaktualizuje
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@responses_lib.activate
def test_hash_not_updated_in_db_after_api_error_on_new_product(erp_file, settings):
    """Ak POST zlyhá, ProductSyncState nesmie byť uložený – pri ďalšom behu sa produkt znovu odošle."""
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])
    responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', status=500)

    sync_products_task()

    assert not ProductSyncState.objects.filter(sku='SKU-001').exists()


@pytest.mark.django_db
@responses_lib.activate
def test_hash_not_updated_in_db_after_api_error_on_existing_product(erp_file, settings):
    """Ak PATCH zlyhá, content_hash v DB musí zostať starý – produkt sa skúsi pri ďalšom behu."""
    settings.ERP_DATA_PATH = erp_file(VALID_ERP_DATA[:1])
    old_hash = 'old-hash-that-does-not-match'
    ProductSyncState.objects.create(
        sku='SKU-001',
        content_hash=old_hash,
        synced_as_new=False,
    )
    responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/SKU-001/', status=500)

    sync_products_task()

    state = ProductSyncState.objects.get(sku='SKU-001')
    assert state.content_hash == old_hash
