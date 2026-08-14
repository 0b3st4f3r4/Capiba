"""Incremental extraction from the Portal da Transparência.

Chunk: transparency
Responsibility: Extract contract and purchase data
from the Portal da Transparência using token authentication.

Dependencies: requests
"""

from __future__ import annotations

import logging
import re
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
) -> list[dict[str, Any]]:
    """Fetches a page from the API with retry and backoff.

    Args:
        url: Endpoint URL.
        params: Query string parameters.
        retries: Maximum number of attempts.
        delay: Base delay between attempts.

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
                fatal_statuses=(400, 401, 403, 422),
                rate_limit_status=429,
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
