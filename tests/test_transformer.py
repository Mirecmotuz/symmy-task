import json
import tempfile
from pathlib import Path

import pytest

from integrator.transformer import (
    compute_hash,
    load_and_transform,
    load_erp_data,
    transform_product,
)


# ---------------------------------------------------------------------------
# transform_product
# ---------------------------------------------------------------------------

class TestTransformProduct:
    def test_basic_transformation(self):
        raw = {
            'id': 'SKU-001',
            'title': 'Kávovar',
            'price_vat_excl': 100.0,
            'stocks': {'praha': 3, 'brno': 2},
            'attributes': {'color': 'červená'},
        }
        result = transform_product(raw)
        assert result['sku'] == 'SKU-001'
        assert result['title'] == 'Kávovar'
        assert result['stock'] == 5
        assert result['price'] == 121.0
        assert result['color'] == 'červená'

    def test_vat_calculation(self):
        raw = {
            'id': 'SKU-X',
            'title': 'Test',
            'price_vat_excl': 1000.0,
            'stocks': {},
            'attributes': {},
        }
        result = transform_product(raw)
        assert result['price'] == 1210.0

    def test_stocks_summed_across_warehouses(self):
        raw = {
            'id': 'SKU-X',
            'title': 'Test',
            'price_vat_excl': 10.0,
            'stocks': {'a': 10, 'b': 20, 'c': 5},
            'attributes': {},
        }
        result = transform_product(raw)
        assert result['stock'] == 35

    def test_missing_color_defaults_to_na(self):
        raw = {
            'id': 'SKU-X',
            'title': 'Test',
            'price_vat_excl': 10.0,
            'stocks': {},
            'attributes': {},
        }
        result = transform_product(raw)
        assert result['color'] == 'N/A'

    def test_null_attributes_defaults_color_to_na(self):
        raw = {
            'id': 'SKU-X',
            'title': 'Test',
            'price_vat_excl': 10.0,
            'stocks': {},
            'attributes': None,
        }
        result = transform_product(raw)
        assert result['color'] == 'N/A'

    def test_null_price_returns_none(self):
        raw = {
            'id': 'SKU-004',
            'title': 'Hrnek',
            'price_vat_excl': None,
            'stocks': {'praha': 10},
            'attributes': {'color': 'černá'},
        }
        assert transform_product(raw) is None

    def test_negative_price_returns_none(self):
        raw = {
            'id': 'SKU-002',
            'title': 'Sleva - chyba',
            'price_vat_excl': -150.0,
            'stocks': {'praha': 10},
            'attributes': {},
        }
        assert transform_product(raw) is None

    def test_non_numeric_stock_value_treated_as_zero(self):
        raw = {
            'id': 'SKU-008',
            'title': 'Filtry',
            'price_vat_excl': 300.0,
            'stocks': {'praha': 'N/A'},
            'attributes': {'color': 'bílá'},
        }
        result = transform_product(raw)
        assert result['stock'] == 0

    def test_zero_price_is_valid(self):
        raw = {
            'id': 'SKU-FREE',
            'title': 'Freebie',
            'price_vat_excl': 0.0,
            'stocks': {},
            'attributes': {},
        }
        result = transform_product(raw)
        assert result is not None
        assert result['price'] == 0.0

    def test_price_rounded_to_two_decimals(self):
        raw = {
            'id': 'SKU-X',
            'title': 'Test',
            'price_vat_excl': 12400.5,
            'stocks': {},
            'attributes': {},
        }
        result = transform_product(raw)
        assert result['price'] == round(12400.5 * 1.21, 2)

    def test_empty_string_color_defaults_to_na(self):
        raw = {
            'id': 'SKU-X',
            'title': 'Test',
            'price_vat_excl': 10.0,
            'stocks': {},
            'attributes': {'color': ''},
        }
        result = transform_product(raw)
        assert result['color'] == 'N/A'


# ---------------------------------------------------------------------------
# load_erp_data – deduplication
# ---------------------------------------------------------------------------

class TestLoadErpData:
    def _write_json(self, data):
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8'
        )
        json.dump(data, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_duplicate_sku_keeps_first_occurrence(self):
        data = [
            {'id': 'SKU-006', 'title': 'First', 'price_vat_excl': 100, 'stocks': {}, 'attributes': {}},
            {'id': 'SKU-006', 'title': 'Second', 'price_vat_excl': 200, 'stocks': {}, 'attributes': {}},
        ]
        path = self._write_json(data)
        result = load_erp_data(path)
        assert len(result) == 1
        assert result[0]['title'] == 'First'

    def test_unique_skus_all_kept(self):
        data = [
            {'id': 'SKU-001', 'title': 'A', 'price_vat_excl': 10, 'stocks': {}, 'attributes': {}},
            {'id': 'SKU-002', 'title': 'B', 'price_vat_excl': 20, 'stocks': {}, 'attributes': {}},
        ]
        path = self._write_json(data)
        result = load_erp_data(path)
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        path = self._write_json([])
        result = load_erp_data(path)
        assert result == []


# ---------------------------------------------------------------------------
# load_and_transform – filters invalid products
# ---------------------------------------------------------------------------

class TestLoadAndTransform:
    def _write_json(self, data):
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8'
        )
        json.dump(data, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_invalid_products_filtered_out(self):
        data = [
            {'id': 'SKU-VALID', 'title': 'OK', 'price_vat_excl': 100, 'stocks': {'a': 1}, 'attributes': {}},
            {'id': 'SKU-NULL', 'title': 'Null price', 'price_vat_excl': None, 'stocks': {}, 'attributes': {}},
            {'id': 'SKU-NEG', 'title': 'Neg price', 'price_vat_excl': -1, 'stocks': {}, 'attributes': {}},
        ]
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8'
        )
        json.dump(data, tmp)
        tmp.close()

        result = load_and_transform(Path(tmp.name))
        assert len(result) == 1
        assert result[0]['sku'] == 'SKU-VALID'

    def test_duplicate_sku_where_first_is_invalid_returns_empty(self):
        # Prvý výskyt SKU má nevalidnú cenu – druhý (validný) sa pri deduplikácii zahodí.
        # Výsledok musí byť prázdny zoznam.
        data = [
            {'id': 'SKU-DUP', 'title': 'Bad', 'price_vat_excl': None, 'stocks': {}, 'attributes': {}},
            {'id': 'SKU-DUP', 'title': 'Good', 'price_vat_excl': 100, 'stocks': {}, 'attributes': {}},
        ]
        path = self._write_json(data)
        result = load_and_transform(path)
        assert result == []


# ---------------------------------------------------------------------------
# compute_hash
# ---------------------------------------------------------------------------

class TestComputeHash:
    def test_same_data_produces_same_hash(self):
        product = {'sku': 'SKU-001', 'title': 'A', 'price': 121.0, 'stock': 5, 'color': 'N/A'}
        assert compute_hash(product) == compute_hash(product)

    def test_different_data_produces_different_hash(self):
        p1 = {'sku': 'SKU-001', 'title': 'A', 'price': 121.0, 'stock': 5, 'color': 'N/A'}
        p2 = {'sku': 'SKU-001', 'title': 'A', 'price': 121.0, 'stock': 6, 'color': 'N/A'}
        assert compute_hash(p1) != compute_hash(p2)

    def test_hash_is_64_chars(self):
        product = {'sku': 'X', 'title': 'Y', 'price': 1.0, 'stock': 0, 'color': 'N/A'}
        assert len(compute_hash(product)) == 64

    def test_key_order_does_not_affect_hash(self):
        p1 = {'sku': 'SKU-001', 'title': 'A', 'price': 121.0, 'stock': 5, 'color': 'N/A'}
        p2 = {'color': 'N/A', 'stock': 5, 'price': 121.0, 'title': 'A', 'sku': 'SKU-001'}
        assert compute_hash(p1) == compute_hash(p2)
