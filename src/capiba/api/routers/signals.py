"""Risk signals endpoint per entity.

Chunk: signals
Responsibility: Serve risk score and detected signals
for a specific CNPJ.

Dependencies: fastapi, capiba.api.services
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Path

from capiba.api import services
from capiba.api.schemas import SignalsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/signals", tags=["signals"])


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
