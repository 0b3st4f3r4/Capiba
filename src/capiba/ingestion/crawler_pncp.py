"""Daily crawler of federal contracts via the PNCP API.

Chunk: pncp
Responsibility: Extract contracts from the Portal Nacional
de Contratações Públicas (PNCP).

Dependencies: requests
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, cast

from capiba.config import PNCP_API_URL
from capiba.ingestion._http import BASE_DELAY, MAX_RETRIES, fetch_page

logger = logging.getLogger(__name__)


def _format_date(value: str | date) -> str:
    """Converts YYYY-MM-DD or datetime.date to the yyyyMMdd required by the PNCP API.

    Args:
        value: Date in YYYY-MM-DD format or a date object.

    Returns:
        Date in yyyyMMdd format.

    Raises:
        ValueError: If the format is invalid.
    """
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    date_str = str(value)
    if re.fullmatch(r"\d{8}", date_str):
        return date_str
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str.replace("-", "")
    raise ValueError(f"Date must be in YYYY-MM-DD or yyyyMMdd format: {date_str}")


def _fetch_page(
    url: str,
    params: dict[str, Any],
    retries: int = MAX_RETRIES,
    delay: float = BASE_DELAY,
) -> dict[str, Any] | None:
    """Fetches a page from the PNCP API with retry and backoff.

    Args:
        url: Endpoint URL.
        params: Query string parameters.
        retries: Maximum number of attempts.
        delay: Base delay between attempts (seconds).

    Returns:
        Dict with the JSON response or None if the response is 204 No Content.

    Raises:
        requests.HTTPError: If the API returns a non-transient error.
    """
    return cast(
        dict[str, Any] | None,
        fetch_page(
            url,
            params,
            retries=retries,
            delay=delay,
            empty_statuses=(204,),
            rate_limit_status=429,
            retry_statuses=(502, 503, 504),
        ),
    )


def fetch_contracts(
    start_date: str | date,
    end_date: str | date,
    agency_cnpj: str | None = None,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Fetches contracts/commitments from PNCP by publication date.

    This endpoint returns already-signed contracts, including supplier
    (niFornecedor) and amounts (valorInicial, valorGlobal).

    Args:
        start_date: Start date in YYYY-MM-DD, yyyyMMdd or date format.
        end_date: End date in YYYY-MM-DD, yyyyMMdd or date format.
        agency_cnpj: CNPJ of the agency that owns the contract (optional).
        page_size: Number of items per page (max 500).

    Returns:
        List of dictionaries with raw contract data.
    """
    start_date_fmt = _format_date(start_date)
    end_date_fmt = _format_date(end_date)

    url = f"{PNCP_API_URL}/v1/contratos"
    results: list[dict[str, Any]] = []
    page = 1
    remaining_pages = None

    while remaining_pages is None or remaining_pages > 0:
        params: dict[str, Any] = {
            "dataInicial": start_date_fmt,
            "dataFinal": end_date_fmt,
            "pagina": page,
            "tamanhoPagina": page_size,
        }
        if agency_cnpj:
            params["cnpjOrgao"] = agency_cnpj

        logger.info(
            "Fetching PNCP contracts page=%s (%s to %s)",
            page,
            start_date_fmt,
            end_date_fmt,
        )

        data = _fetch_page(url, params)

        if data is None:
            break

        page_data = data.get("data", [])
        results.extend(page_data)

        remaining_pages = data.get("paginasRestantes", 0)
        if not page_data or remaining_pages == 0:
            break

        page += 1

    logger.info("Total PNCP contracts returned: %d", len(results))
    return results
