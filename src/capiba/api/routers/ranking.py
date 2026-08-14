"""Municipal ranking endpoint.

Chunk: ranking
Responsibility: Serve municipality ranking by favoritism
risk index.

Dependencies: fastapi, capiba.api.services
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Query

from capiba.api import services
from capiba.api.schemas import RankingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ranking", tags=["ranking"])


@router.get("/municipalities", response_model=RankingResponse)
async def get_ranking(
    uf: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> RankingResponse:
    """Returns municipality ranking by risk.

    Args:
        uf: State filter (optional).
        period_start: Period start date.
        period_end: Period end date.
        limit: Maximum number of results.

    Returns:
        RankingResponse with list ordered by risk.

    Raises:
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    rows = services.aggregate_ranking(db, uf, period_start, period_end)
    return services.build_ranking(rows, period_start, period_end, limit)
