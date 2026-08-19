"""Risk signals endpoint per entity.

Chunk: signals
Responsibility: Serve risk score and detected signals
for a specific CNPJ, and the reproducible evidence packages
(O9) linked to a signal key.

Dependencies: fastapi, capiba.api.services, capiba.evidence.storage
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from capiba.api import services
from capiba.api.routers.evidence import get_storage
from capiba.api.schemas import EvidenceItem, SignalsResponse
from capiba.evidence.storage import EvidenceStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/signals", tags=["signals"])

_HTTP_503_DETAIL = "Evidence storage unavailable"


@router.get("/{cnpj}", response_model=SignalsResponse)
async def get_signals(
    cnpj: str = Path(..., pattern=r"^\d{14}$"),
) -> SignalsResponse:
    """Returns risk signals for a CNPJ.

    Args:
        cnpj: Entity CNPJ (14 digits, no formatting).

    Returns:
        SignalsResponse with risk index and detected signals.
        A CNPJ without contracts returns empty signals and risk 0.

    Raises:
        HTTPException 422: If the CNPJ is invalid.
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    contracts = services.fetch_supplier_contracts(db, cnpj)
    return services.compute_signals(cnpj, contracts, db)


@router.get("/{key}/evidence", response_model=list[EvidenceItem])
async def list_signal_evidence(
    key: str,
    storage: Annotated[EvidenceStorage, Depends(get_storage)],
) -> list[EvidenceItem]:
    """Lists the reproducible evidence packages (O9) of a signal.

    ``key`` is the triage key ``{entity_type}:{entity_id}:{signal_type}``
    (O10). Manifests reference the run batch package via
    ``batch_sha256``; the package content is downloaded via
    ``GET /v1/evidence/{sha256}``.

    Raises:
        HTTPException 503: If the storage is unavailable.
    """
    try:
        items = storage.list_by_signal(key)
    except Exception as exc:
        logger.warning("Signal evidence listing failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return [EvidenceItem(**item) for item in items]
