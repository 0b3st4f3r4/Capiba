"""Daily crawler of municipal official gazettes via the Querido Diário API.

Chunk: querido_diario
Responsibility: Extract gazette metadata (and the extracted plain text)
of one pilot municipality from the Querido Diário project (OKBR, MIT).

Dependencies: requests, capiba.ingestion._http
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date
from typing import Any, cast

import requests

from capiba.config import QUERIDO_DIARIO_API_URL
from capiba.ingestion._http import (
    BASE_DELAY,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    fetch_page,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


def _fetch_page(params: dict[str, Any]) -> dict[str, Any]:
    """Fetches one page of the /gazettes endpoint with retry and backoff."""
    return cast(
        dict[str, Any],
        fetch_page(
            f"{QUERIDO_DIARIO_API_URL}/gazettes",
            params,
            rate_limit_status=429,
            retry_statuses=(500, 502, 503, 504),
        ),
    )


def fetch_gazettes(
    territory_id: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetches the gazette metadata of a territory over a publication window.

    Paginates the Querido Diário ``/gazettes`` endpoint (size/offset). Each
    record carries ``territory_id``, ``territory_name``, ``state_code``,
    ``date``, ``edition``, ``is_extra_edition``, ``scraped_at``, ``url``
    (original PDF) and ``txt_url`` (extracted plain text).

    Args:
        territory_id: 7-digit IBGE id of the municipality (e.g. ``2611606``
            for Recife).
        start_date: First publication date (inclusive).
        end_date: Last publication date (inclusive).

    Returns:
        List of raw gazette records.
    """
    records: list[dict[str, Any]] = []
    offset = 0

    while True:
        params: dict[str, Any] = {
            "territory_ids": [territory_id],
            "published_since": start_date.isoformat(),
            "published_until": end_date.isoformat(),
            "size": PAGE_SIZE,
            "offset": offset,
            "sort_by": "ascending_date",
        }
        logger.info(
            "Fetching Querido Diário gazettes offset=%d (%s to %s, %s)",
            offset,
            start_date,
            end_date,
            territory_id,
        )
        payload = _fetch_page(params)

        gazettes = payload.get("gazettes", [])
        records.extend(gazettes)
        offset += len(gazettes)

        if not gazettes or offset >= payload.get("total_gazettes", 0):
            break

    logger.info(
        "Total Querido Diário gazettes returned: %d (%s)", len(records), territory_id
    )
    return records


def download_gazette_text(url: str) -> bytes:
    """Downloads the extracted plain text of a gazette (``txt_url``).

    The payload is plain text extracted from the official PDF — no markup
    structure. Retries transient network/5xx failures with backoff; other
    HTTP errors raise immediately.

    Args:
        url: URL of the extracted text file.

    Returns:
        The raw file contents.

    Raises:
        requests.HTTPError: On non-transient HTTP errors.
        requests.RequestException: After exhausting the retries.
    """
    last_exception: requests.RequestException
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.content
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status < 500 or attempt == MAX_RETRIES:
                raise
            last_exception = exc
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            last_exception = exc
        logger.warning(
            "Error downloading gazette text %s (attempt %d/%d): %s",
            url,
            attempt,
            MAX_RETRIES,
            last_exception,
        )
        time.sleep(BASE_DELAY * (2 ** (attempt - 1)))
    raise RuntimeError(f"unreachable: download_gazette_text({url})")  # pragma: no cover


def text_file_name(record: dict[str, Any]) -> str:
    """Builds the deterministic bronze file name for a gazette's text.

    ``<territory_id>-<date>-<sha256(url)[:12]>.txt`` — deterministic per
    gazette, so a retried task skips texts already in the bronze layer.
    """
    digest = hashlib.sha256(str(record["url"]).encode()).hexdigest()[:12]
    return f"{record['territory_id']}-{record['date']}-{digest}.txt"
