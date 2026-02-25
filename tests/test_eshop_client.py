import json
import threading
import time
from unittest.mock import patch

import pytest
import requests
import responses as responses_lib

from integrator.eshop_client import EshopClient, RateLimiter

BASE_URL = 'https://api.fake-eshop.cz/v1'
PRODUCT = {'sku': 'SKU-001', 'title': 'Kávovar', 'price': 15004.61, 'stock': 8, 'color': 'stříbrná'}


@pytest.fixture(autouse=True)
def override_settings(settings):
    settings.ESHOP_API_BASE_URL = BASE_URL
    settings.ESHOP_API_KEY = 'symma-secret-token'


# ---------------------------------------------------------------------------
# POST – new product
# ---------------------------------------------------------------------------

class TestPostNewProduct:
    @responses_lib.activate
    def test_post_sends_to_correct_url(self):
        responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)
        client = EshopClient()
        client.send_product(PRODUCT, is_new=True)
        assert len(responses_lib.calls) == 1
        assert responses_lib.calls[0].request.url == f'{BASE_URL}/products/'

    @responses_lib.activate
    def test_post_includes_api_key_header(self):
        responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)
        client = EshopClient()
        client.send_product(PRODUCT, is_new=True)
        assert responses_lib.calls[0].request.headers['X-Api-Key'] == 'symma-secret-token'


# ---------------------------------------------------------------------------
# PATCH – existing product
# ---------------------------------------------------------------------------

class TestPatchExistingProduct:
    @responses_lib.activate
    def test_patch_sends_to_correct_url(self):
        sku = PRODUCT['sku']
        responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/{sku}/', json={}, status=200)
        client = EshopClient()
        client.send_product(PRODUCT, is_new=False)
        assert responses_lib.calls[0].request.url == f'{BASE_URL}/products/{sku}/'

    @responses_lib.activate
    def test_patch_includes_api_key_header(self):
        """(4) API key header musí byť prítomný aj pri PATCH, nielen pri POST."""
        sku = PRODUCT['sku']
        responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/{sku}/', json={}, status=200)
        client = EshopClient()
        client.send_product(PRODUCT, is_new=False)
        assert responses_lib.calls[0].request.headers['X-Api-Key'] == 'symma-secret-token'


# ---------------------------------------------------------------------------
# Retry on 429
# ---------------------------------------------------------------------------

class TestRetryOn429:
    @responses_lib.activate
    def test_retry_after_header_respected(self):
        url = f'{BASE_URL}/products/'
        responses_lib.add(
            responses_lib.POST, url,
            status=429,
            headers={'Retry-After': '0'},
        )
        responses_lib.add(responses_lib.POST, url, json={}, status=201)

        with patch('integrator.eshop_client.time.sleep') as mock_sleep:
            client = EshopClient()
            resp = client.send_product(PRODUCT, is_new=True)

        assert resp.status_code == 201
        assert len(responses_lib.calls) == 2
        mock_sleep.assert_called_once_with(0.0)

    @responses_lib.activate
    def test_exponential_backoff_when_no_retry_after(self):
        url = f'{BASE_URL}/products/'
        responses_lib.add(responses_lib.POST, url, status=429)
        responses_lib.add(responses_lib.POST, url, status=429)
        responses_lib.add(responses_lib.POST, url, json={}, status=201)

        with patch('integrator.eshop_client.time.sleep') as mock_sleep:
            client = EshopClient()
            resp = client.send_product(PRODUCT, is_new=True)

        assert resp.status_code == 201
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args[0] == 1.0
        assert sleep_args[1] == 2.0

    @responses_lib.activate
    def test_raises_after_max_retries_exceeded(self):
        url = f'{BASE_URL}/products/'
        for _ in range(3):
            responses_lib.add(responses_lib.POST, url, status=429)

        with patch('integrator.eshop_client.time.sleep'):
            client = EshopClient()
            with pytest.raises(RuntimeError, match='rate limiting'):
                client.send_product(PRODUCT, is_new=True)

# ---------------------------------------------------------------------------
# Rate limiting – 5 req/s
# ---------------------------------------------------------------------------

class TestRateLimiting:
    @responses_lib.activate
    def test_first_five_requests_are_immediate(self):
        """Fixed window starts full – first 5 requests consume tokens with no waiting."""
        url = f'{BASE_URL}/products/'
        for _ in range(5):
            responses_lib.add(responses_lib.POST, url, json={}, status=201)

        client = EshopClient()
        start = time.monotonic()
        for _ in range(5):
            client.send_product(PRODUCT, is_new=True)
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"First 5 requests should be near-instant, got {elapsed:.2f}s"

    @responses_lib.activate
    def test_sixth_request_waits_for_new_window(self):
        """After 5 requests the window is exhausted – 6th must wait ~1s for a new window."""
        url = f'{BASE_URL}/products/'
        for _ in range(6):
            responses_lib.add(responses_lib.POST, url, json={}, status=201)

        client = EshopClient()
        for _ in range(5):
            client.send_product(PRODUCT, is_new=True)

        start = time.monotonic()
        client.send_product(PRODUCT, is_new=True)
        elapsed = time.monotonic() - start

        assert elapsed >= 0.9, f"6th request should wait ~1s for new window, got {elapsed:.2f}s"

    @responses_lib.activate
    def test_second_window_requests_are_immediate(self):
        """After window reset, requests proceed immediately again (up to 5)."""
        url = f'{BASE_URL}/products/'
        for _ in range(7):
            responses_lib.add(responses_lib.POST, url, json={}, status=201)

        client = EshopClient()
        for _ in range(6):   # exhaust first window + trigger reset
            client.send_product(PRODUCT, is_new=True)

        start = time.monotonic()
        client.send_product(PRODUCT, is_new=True)   # 2nd token of new window
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"Request in new window should be immediate, got {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# (1) HTTP chyby mimo 429 – okamžité vyhodenie bez retryu
# ---------------------------------------------------------------------------

class TestHttpErrors:
    @pytest.mark.parametrize('status_code', [400, 404, 500])
    @responses_lib.activate
    def test_non_429_error_raises_http_error_immediately(self, status_code):
        """4xx/5xx (okrem 429) musia vyhodiť HTTPError hneď – bez akéhokoľvek retryu."""
        url = f'{BASE_URL}/products/'
        responses_lib.add(responses_lib.POST, url, json={'error': 'fail'}, status=status_code)

        client = EshopClient()
        with pytest.raises(requests.HTTPError):
            client.send_product(PRODUCT, is_new=True)

        assert len(responses_lib.calls) == 1, "Pri non-429 chybe sa nesmie opakovať request"


# ---------------------------------------------------------------------------
# (2) Telo requestu – product dict musí byť odoslaný ako JSON
# ---------------------------------------------------------------------------

class TestRequestPayload:
    @responses_lib.activate
    def test_post_sends_product_as_json_body(self):
        responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)
        client = EshopClient()
        client.send_product(PRODUCT, is_new=True)

        sent_body = json.loads(responses_lib.calls[0].request.body)
        assert sent_body == PRODUCT

    @responses_lib.activate
    def test_patch_sends_product_as_json_body(self):
        sku = PRODUCT['sku']
        responses_lib.add(responses_lib.PATCH, f'{BASE_URL}/products/{sku}/', json={}, status=200)
        client = EshopClient()
        client.send_product(PRODUCT, is_new=False)

        sent_body = json.loads(responses_lib.calls[0].request.body)
        assert sent_body == PRODUCT


# ---------------------------------------------------------------------------
# (3) Neplatný Retry-After header – fallback na exponenciálny backoff
# ---------------------------------------------------------------------------

class TestInvalidRetryAfterHeader:
    @responses_lib.activate
    def test_non_numeric_retry_after_falls_back_to_backoff(self):
        """Ak je Retry-After nečíselná hodnota, klient musí použiť backoff (1 s)."""
        url = f'{BASE_URL}/products/'
        responses_lib.add(
            responses_lib.POST, url,
            status=429,
            headers={'Retry-After': 'next-tuesday'},
        )
        responses_lib.add(responses_lib.POST, url, json={}, status=201)

        with patch('integrator.eshop_client.time.sleep') as mock_sleep:
            client = EshopClient()
            resp = client.send_product(PRODUCT, is_new=True)

        assert resp.status_code == 201
        mock_sleep.assert_called_once_with(1.0)


# ---------------------------------------------------------------------------
# (5) Normalizácia trailing slash v base URL
# ---------------------------------------------------------------------------

class TestBaseUrlNormalization:
    @responses_lib.activate
    def test_trailing_slash_in_base_url_does_not_duplicate(self, settings):
        """rstrip('/') v konštruktore nesmie spôsobiť double-slash v URL."""
        settings.ESHOP_API_BASE_URL = BASE_URL + '/'
        responses_lib.add(responses_lib.POST, f'{BASE_URL}/products/', json={}, status=201)

        client = EshopClient()
        client.send_product(PRODUCT, is_new=True)

        assert responses_lib.calls[0].request.url == f'{BASE_URL}/products/'


# ---------------------------------------------------------------------------
# (6) Thread safety RateLimitera
# ---------------------------------------------------------------------------

class TestRateLimiterThreadSafety:
    def test_concurrent_acquires_complete_without_errors(self):
        """Pod súbežnou záťažou nesmie RateLimiter vyhodiť výnimku ani pokaziť stav tokenov."""
        limiter = RateLimiter(rate=5)
        errors = []

        def acquire():
            try:
                limiter.acquire()
            except Exception as exc:
                errors.append(exc)

        with patch('integrator.eshop_client.time.sleep'):
            threads = [threading.Thread(target=acquire) for _ in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Vlákna vyhodili chyby: {errors}"
        assert limiter._tokens >= 0, "Počet tokenov nesmie byť záporný"
