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

from capiba.config import PNCP_API_URL, PNCP_TERMS_API_URL
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
            retry_statuses=(500, 502, 503, 504),
        ),
    )


def _fetch_all(
    url: str,
    start_date: str | date,
    end_date: str | date,
    agency_cnpj: str | None = None,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Paginates a PNCP contract endpoint over a date window.

    Args:
        url: Endpoint URL (``/v1/contratos`` or ``/v1/contratos/atualizacao``).
        start_date: Start date in YYYY-MM-DD, yyyyMMdd or date format.
        end_date: End date in YYYY-MM-DD, yyyyMMdd or date format.
        agency_cnpj: CNPJ of the agency that owns the contract (optional).
        page_size: Number of items per page (max 500).

    Returns:
        List of dictionaries with raw contract data.
    """
    start_date_fmt = _format_date(start_date)
    end_date_fmt = _format_date(end_date)

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
            "Fetching PNCP contracts page=%s (%s to %s, %s)",
            page,
            start_date_fmt,
            end_date_fmt,
            url.rsplit("/", 1)[-1],
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

    logger.info("Total PNCP contracts returned: %d (%s)", len(results), url)
    return results


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
    return _fetch_all(
        f"{PNCP_API_URL}/v1/contratos", start_date, end_date, agency_cnpj, page_size
    )


def fetch_contract_updates(
    start_date: str | date,
    end_date: str | date,
    agency_cnpj: str | None = None,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Fetches contracts from PNCP by *update* date (PR-D-05).

    Same payload as ``/v1/contratos`` (valorInicial, valorAcumulado,
    numeroRetificacao, vigências), keyed by the date of the last update —
    captures contracts amended after their original publication. The
    bronze observations feed the amendment red flags
    (``capiba.detection.amendments``).

    Args:
        start_date: Start date in YYYY-MM-DD, yyyyMMdd or date format.
        end_date: End date in YYYY-MM-DD, yyyyMMdd or date format.
        agency_cnpj: CNPJ of the agency that owns the contract (optional).
        page_size: Number of items per page (max 500).

    Returns:
        List of dictionaries with raw contract data.
    """
    return _fetch_all(
        f"{PNCP_API_URL}/v1/contratos/atualizacao",
        start_date,
        end_date,
        agency_cnpj,
        page_size,
    )


_CONTROL_NUMBER_RE = re.compile(
    r"^(?P<cnpj>\d{14})-(?P<unidade>\d+)-(?P<sequencial>\d+)/(?P<ano>\d{4})$"
)


def parse_control_number(numero_controle: str) -> tuple[str, int, int]:
    """Splits a ``numeroControlePNCP`` into its terms-endpoint path parts.

    The control number is ``{cnpj}-{unidade}-{sequencial}/{ano}`` (e.g.
    ``28414217000167-2-000003/2026``); the terms endpoint takes the cnpj,
    the year and the sequence as an integer.

    Args:
        numero_controle: PNCP control number of the contract.

    Returns:
        Tuple ``(cnpj, ano, sequencial)``.

    Raises:
        ValueError: If the control number does not match the layout.
    """
    match = _CONTROL_NUMBER_RE.fullmatch(str(numero_controle).strip())
    if match is None:
        raise ValueError(f"Invalid numeroControlePNCP: {numero_controle}")
    return match.group("cnpj"), int(match.group("ano")), int(match.group("sequencial"))


def fetch_contract_terms(
    cnpj: str,
    ano: int,
    sequencial: int,
    page_size: int = 500,
    retries: int = MAX_RETRIES,
    delay: float = BASE_DELAY,
) -> list[dict[str, Any]] | None:
    """Fetches the registered terms (aditivos) of one contract (PR-D-05b).

    ``GET /v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos`` of the
    transactional ``pncp`` API group (public, no auth — verified live on
    2026-08-21; absent from the ``consulta`` group). The authoritative
    source for "was there a formal amendment?": only the vigente terms are
    listed (excluded terms and publication retifications stay out). One
    request per contract — terms are few, a single ``page_size`` page
    suffices; the crawl cost is what restricts the pilot cut.

    Args:
        cnpj: CNPJ of the agency that owns the contract (14 digits).
        ano: Contract year (path part of the control number).
        sequencial: Contract sequence within the year, as an integer.
        page_size: Page size requested (terms are rarely more than a few).
        retries: Maximum number of attempts.
        delay: Base delay between attempts (seconds).

    Returns:
        List of raw term payloads, or None when the contract has no terms
        (HTTP 204 — "no terms" is data: the flags compute 0).

    Raises:
        requests.HTTPError: If the API returns a non-transient error; the
            caller records NULL flags (insufficient data), never 0.
    """
    url = f"{PNCP_TERMS_API_URL}/v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos"
    return cast(
        list[dict[str, Any]] | None,
        fetch_page(
            url,
            {"pagina": 1, "tamanhoPagina": page_size},
            retries=retries,
            delay=delay,
            empty_statuses=(204,),
            rate_limit_status=429,
            retry_statuses=(500, 502, 503, 504),
        ),
    )
