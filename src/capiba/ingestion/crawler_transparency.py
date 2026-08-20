"""Incremental extraction from the Portal da Transparência.

Chunk: transparency
Responsibility: Extract contract and purchase data
from the Portal da Transparência using token authentication.

Dependencies: requests
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from capiba.config import (
    TRANSPARENCY_AGENCY_CODES,
    TRANSPARENCY_API_KEY,
    TRANSPARENCY_API_URL,
)
from capiba.ingestion._http import BASE_DELAY, MAX_RETRIES, fetch_page

logger = logging.getLogger(__name__)

PAGE_SIZE = 15_000  # documented maximum

# Sanction lists exposed by the API (GET /ceis and GET /cnep).
SANCTION_LISTS: tuple[str, ...] = ("ceis", "cnep")


def _headers() -> dict[str, str]:
    """Returns the required headers for the Portal da Transparência API."""
    if not TRANSPARENCY_API_KEY:
        logger.warning("TRANSPARENCY_API_KEY not configured. Requests will be blocked.")
    return {
        "chave-api-dados": TRANSPARENCY_API_KEY,
        "Accept": "application/json",
    }


def _format_date(date_str: str) -> str:
    """Converts YYYY-MM-DD to the DD/MM/YYYY required by the API.

    Args:
        date_str: Date in YYYY-MM-DD or DD/MM/YYYY format.

    Returns:
        Date in DD/MM/YYYY format.

    Raises:
        ValueError: If the format is invalid.
    """
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", date_str):
        return date_str
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        parts = date_str.split("-")
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    raise ValueError(f"Date must be in YYYY-MM-DD or DD/MM/YYYY format: {date_str}")


def _fetch_page(
    url: str,
    params: dict[str, Any],
    retries: int = MAX_RETRIES,
    delay: float = BASE_DELAY,
    fatal_statuses: tuple[int, ...] = (400, 401, 403, 422),
    retry_statuses: tuple[int, ...] = (500, 502, 503, 504),
) -> list[dict[str, Any]]:
    """Fetches a page from the API with retry and backoff.

    Args:
        url: Endpoint URL.
        params: Query string parameters.
        retries: Maximum number of attempts.
        delay: Base delay between attempts.
        fatal_statuses: HTTP statuses that abort immediately (non-transient).
        retry_statuses: Transient statuses retried with the long delay.

    Returns:
        List of records from the page.

    Raises:
        requests.HTTPError: On non-transient errors.
        RuntimeError: If the API_KEY is not configured.
    """
    if not TRANSPARENCY_API_KEY:
        raise RuntimeError(
            "TRANSPARENCY_API_KEY not configured. "
            "Register at https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email"
        )

    return (
        cast(
            list[dict[str, Any]],
            fetch_page(
                url,
                params,
                headers=_headers(),
                retries=retries,
                delay=delay,
                fatal_statuses=fatal_statuses,
                rate_limit_status=429,
                retry_statuses=retry_statuses,
            ),
        )
        or []
    )


def fetch_contracts(
    start_date: str,
    end_date: str,
    agency_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetches federal contracts by period.

    Args:
        start_date: Start date in YYYY-MM-DD or DD/MM/YYYY format.
        end_date: End date in YYYY-MM-DD or DD/MM/YYYY format.
        agency_codes: List of SIAFI agency codes. If empty, uses a
            default list of Federal Executive Branch agencies.

    Returns:
        List of raw contracts.
    """
    agencies = agency_codes if agency_codes else TRANSPARENCY_AGENCY_CODES
    if not agencies:
        agencies = ["26000"]  # Ministry of Finance/Planning as fallback

    url = f"{TRANSPARENCY_API_URL}/contratos"
    start_date_fmt = _format_date(start_date)
    end_date_fmt = _format_date(end_date)

    results: list[dict[str, Any]] = []
    for code in agencies:
        params: dict[str, Any] = {
            "dataInicio": start_date_fmt,
            "dataFim": end_date_fmt,
            "pagina": 1,
            "codigoOrgao": code,
        }

        logger.info(
            "Fetching Transparency contracts: %s to %s (agency=%s)",
            start_date_fmt,
            end_date_fmt,
            code,
        )

        results.extend(_fetch_page(url, params))

    return results


def fetch_sanctions(
    list_name: str,
    cnpj: str | None = None,
    max_pages: int | None = None,
    start_page: int = 1,
    on_page: Callable[[int, list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """Fetches a sanction list (CEIS/CNEP), paginating until an empty page.

    The lists are full snapshots (no temporal filter); the walk stops on the
    first empty page. ``start_page``/``on_page`` support incremental crawls:
    the caller can persist each page as it lands and, after a failure, resume
    from the next unpersisted page instead of restarting the whole walk.

    The sanction endpoints answer sporadic ``400`` responses deep into the
    walk (observed at page 352 of a 770+ page crawl that had fetched the
    same page fine minutes earlier), so 400 is retried with the long delay
    here instead of aborting immediately as in the contracts endpoint.

    A missing ``TRANSPARENCY_API_KEY`` raises ``RuntimeError`` like the other
    endpoints of this crawler.

    Args:
        list_name: Which list to fetch (``ceis`` or ``cnep``).
        cnpj: Optional ``cnpjSancionado`` filter.
        max_pages: Optional cap on the number of pages (tests/backfills).
        start_page: First page to fetch (1-based).
        on_page: Optional callback invoked with ``(page, records)`` after
            each non-empty page lands.

    Returns:
        List of raw sanction records.

    Raises:
        ValueError: If the list name is unknown.
        RuntimeError: If the API key is not configured.
        requests.HTTPError: On non-transient API errors.
    """
    if list_name not in SANCTION_LISTS:
        raise ValueError(
            f"Unknown sanction list '{list_name}' (known: {SANCTION_LISTS})"
        )

    url = f"{TRANSPARENCY_API_URL}/{list_name}"
    results: list[dict[str, Any]] = []
    page = start_page
    fetched = 0
    while True:
        params: dict[str, Any] = {"pagina": page}
        if cnpj:
            params["cnpjSancionado"] = cnpj

        logger.info("Fetching Transparency %s sanctions: page %d", list_name, page)
        records = _fetch_page(
            url,
            params,
            fatal_statuses=(401, 403, 422),
            retry_statuses=(400, 500, 502, 503, 504),
        )
        if not records:
            break
        results.extend(records)
        if on_page is not None:
            on_page(page, records)
        fetched += 1
        if max_pages is not None and fetched >= max_pages:
            break
        page += 1

    return results


def fetch_purchases(
    year: int,
    month: int,
    agency_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetches government purchases by period.

    Kept for compatibility. The Portal da Transparência has no
    specific "purchases" endpoint; we use contracts as the source.

    Args:
        year: Reference year.
        month: Reference month (1-12).
        agency_codes: List of SIAFI agency codes.

    Returns:
        List of contracts for the period.
    """
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    return fetch_contracts(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        agency_codes=agency_codes,
    )
