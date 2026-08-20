"""Graph endpoints.

Chunk: graphs
Responsibility: Expose the ArangoDB graph operators (ownership tracing,
partners of a buyer's suppliers, FtM JSON export), with lazy
infrastructure instantiation (get_db) so the app starts offline.

Dependencies: fastapi, capiba.detection.graphs, capiba.db.ftm
"""

from __future__ import annotations

import logging
from typing import Annotated

from arango.database import StandardDatabase
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from capiba.api.schemas import (
    FtmExportResponse,
    OwnershipResponse,
    PartnerOfBuyer,
    PartnersResponse,
)
from capiba.db.arangodb import get_capiba_db
from capiba.db.ftm import export_ftm_entities
from capiba.detection.graphs import partners_of_buyer, trace_ownership

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/graph", tags=["graph"])

_HTTP_503_DETAIL = "ArangoDB database unavailable"


def get_db() -> StandardDatabase:
    """Instantiates the ArangoDB connection lazily.

    Deferred to request time so the app imports and starts offline;
    tests override this dependency with a mock.

    Raises:
        HTTPException 503: If the database is unavailable.
    """
    try:
        return get_capiba_db()
    except Exception as exc:
        logger.warning("ArangoDB unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc


@router.get("/ownership/{cnpj}", response_model=OwnershipResponse)
async def get_ownership(
    db: Annotated[StandardDatabase, Depends(get_db)],
    cnpj: str = Path(..., pattern=r"^\d{14}$"),
    max_depth: int = Query(default=3, ge=1, le=10),
) -> OwnershipResponse:
    """Traces the ownership chain (beneficial ownership) of a company.

    Best-effort over the ArangoDB graph: an unknown CNPJ simply returns
    no paths.

    Args:
        cnpj: Company CNPJ (14 digits, no formatting).
        max_depth: Maximum traversal depth over the ``ownership`` edges
            (1-10).

    Raises:
        HTTPException 422: If the CNPJ is invalid.
        HTTPException 503: If the database is unavailable.
    """
    try:
        paths = trace_ownership(cnpj, max_depth=max_depth, db=db)
    except Exception as exc:
        logger.warning("Ownership tracing failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return OwnershipResponse(entity=cnpj, max_depth=max_depth, paths=paths)


@router.get("/partners/{siafi_code}", response_model=PartnersResponse)
async def get_partners_of_buyer(
    db: Annotated[StandardDatabase, Depends(get_db)],
    siafi_code: str = Path(..., pattern=r"^\d+$"),
) -> PartnersResponse:
    """Lists the partners (sócios) of the suppliers of a buying agency.

    Answered by a graph traversal (contracts of
    the buyer → supplier CNPJ → FtM company → inbound
    ownership/directorship).

    Args:
        siafi_code: SIAFI code of the buying agency.

    Raises:
        HTTPException 422: If the SIAFI code is invalid.
        HTTPException 503: If the database is unavailable.
    """
    try:
        partners = partners_of_buyer(siafi_code, db=db)
    except Exception as exc:
        logger.warning("Partners-of-buyer traversal failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return PartnersResponse(
        siafi_code=siafi_code,
        partners=[PartnerOfBuyer(**row) for row in partners],
    )


@router.get("/ftm/{cnpj}", response_model=FtmExportResponse)
async def get_ftm_export(
    db: Annotated[StandardDatabase, Depends(get_db)],
    cnpj: str = Path(..., pattern=r"^\d{14}$"),
) -> FtmExportResponse:
    """Exports the subgraph around a company as FtM JSON entities.

    Args:
        cnpj: Company CNPJ (14 digits, no formatting).

    Raises:
        HTTPException 422: If the CNPJ is invalid.
        HTTPException 503: If the database is unavailable.
    """
    try:
        entities = export_ftm_entities(cnpj, db=db)
    except Exception as exc:
        logger.warning("FtM export failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return FtmExportResponse(entity=cnpj, entities=entities)
