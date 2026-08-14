"""HTTP helpers shared by the ingestion crawlers.

Chunk: http_ingestion
Responsibility: Centralize the GET request logic with
retry and exponential backoff used by the crawlers.

Dependencies: requests
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 2.0
REQUEST_TIMEOUT = 90
RATE_LIMIT_DELAY = 30.0


def fetch_page(
    url: str,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
    retries: int = MAX_RETRIES,
    delay: float = BASE_DELAY,
    fatal_statuses: tuple[int, ...] = (400, 422),
    empty_statuses: tuple[int, ...] = (),
    rate_limit_status: int | None = None,
) -> Any | None:
    """Fetches an API page with retry and exponential backoff.

    Args:
        url: Endpoint URL.
        params: Query string parameters.
        headers: Optional HTTP headers.
        retries: Maximum number of attempts.
        delay: Base delay between attempts (seconds).
        fatal_statuses: HTTP statuses that abort immediately (non-transient).
        empty_statuses: HTTP statuses treated as an empty response (returns None).
        rate_limit_status: Rate limit HTTP status; waits `delay` and tries again.

    Returns:
        JSON response (dict or list, depending on the API), or None if the
        status is in empty_statuses.

    Raises:
        requests.HTTPError: If the API returns a non-transient error.
    """
    last_exception: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code in empty_statuses:
                return None

            if (
                rate_limit_status is not None
                and response.status_code == rate_limit_status
            ):
                wait = RATE_LIMIT_DELAY if delay == BASE_DELAY else delay
                logger.warning("Rate limit reached. Waiting %.1fs...", wait)
                time.sleep(wait)
                continue

            response.raise_for_status()
            return cast(Any, response.json())
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in fatal_statuses:
                raise
            last_exception = exc
            logger.warning(
                "Error fetching page %s (attempt %d/%d): %s",
                params.get("pagina"),
                attempt,
                retries,
                exc,
            )
        except requests.RequestException as exc:
            last_exception = exc
            logger.warning(
                "Network error on page %s (attempt %d/%d): %s",
                params.get("pagina"),
                attempt,
                retries,
                exc,
            )

        if attempt < retries:
            sleep_time = delay * (2 ** (attempt - 1))
            logger.info("Waiting %.1fs before retry", sleep_time)
            time.sleep(sleep_time)

    if isinstance(last_exception, Exception):
        raise last_exception
    return None
