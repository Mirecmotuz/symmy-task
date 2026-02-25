import logging
import time
from threading import Lock

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RATE_LIMIT = 5  # requests per second


class RateLimiter:
    """
    Fixed-window rate limiter (thread-safe).

    Allows up to `rate` requests per 1-second window.
    The window starts on the first request. While tokens remain, requests
    proceed immediately. When the bucket is empty, the limiter sleeps until
    the current window expires, then opens a fresh window with a full bucket.
    If no requests arrive before a window expires, the next request simply
    starts a new window.
    """

    def __init__(self, rate: int):
        self._rate = rate
        self._tokens = rate
        self._window_start = None   # window starts lazily on first request
        self._lock = Lock()

    def acquire(self):
        with self._lock:
            now = time.monotonic()

            # Start a new window if there is none yet, or the current one expired.
            if self._window_start is None or (now - self._window_start) >= 1.0:
                self._window_start = now
                self._tokens = self._rate

            if self._tokens > 0:
                self._tokens -= 1
            else:
                # Wait until the end of the current window, then open a new one.
                wait = 1.0 - (now - self._window_start)
                if wait > 0:
                    time.sleep(wait)
                self._window_start = time.monotonic()
                self._tokens = self._rate - 1   # consume 1 token for this request


class EshopClient:
    def __init__(self):
        self._base_url = settings.ESHOP_API_BASE_URL.rstrip('/')
        self._session = requests.Session()
        self._session.headers.update({'X-Api-Key': settings.ESHOP_API_KEY})
        self._rate_limiter = RateLimiter(RATE_LIMIT)

    def send_product(self, product: dict, is_new: bool) -> requests.Response:
        """Send a product to the eshop API. POST for new, PATCH for existing."""
        sku = product['sku']
        if is_new:
            url = f"{self._base_url}/products/"
            method = 'POST'
        else:
            url = f"{self._base_url}/products/{sku}/"
            method = 'PATCH'

        return self._request_with_retry(method, url, json=product)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        backoff = 1.0
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limiter.acquire()
            response = self._session.request(method, url, **kwargs)

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response)
                wait = retry_after if retry_after is not None else backoff
                logger.warning(
                    "429 Too Many Requests (attempt %d/%d). Waiting %.1fs before retry.",
                    attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                backoff *= 2
                continue

            response.raise_for_status()
            return response

        raise RuntimeError(
            f"API request {method} {url} failed after {MAX_RETRIES} retries due to rate limiting."
        )

    @staticmethod
    def _parse_retry_after(response: requests.Response):
        """Return float seconds from Retry-After header, or None if absent/invalid."""
        header = response.headers.get('Retry-After')
        if header is None:
            return None
        try:
            return float(header)
        except (TypeError, ValueError):
            return None
