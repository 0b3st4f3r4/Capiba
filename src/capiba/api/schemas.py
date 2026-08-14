"""API Pydantic schemas.

Responsibility: typed request/response models
for all endpoints of the signals API.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class SignalType(StrEnum):
    """Detection signal types."""

    SINGLE_BID = "single_bid"
    CONCENTRATION = "concentration"
    COLLUSION_NETWORK = "collusion_network"
    ANOMALOUS_PRICE = "anomalous_price"
    SEMANTIC_GAP = "semantic_gap"
    ANOMALOUS_DURATION = "anomalous_duration"


class Signal(BaseModel):
    """Individual detection signal."""

    type: SignalType
    score: float = Field(..., ge=0.0, le=1.0)
    evidence: str | None = None


class SignalsResponse(BaseModel):
    """Response of the /v1/signals/{cnpj} endpoint."""

    entity: str = Field(..., pattern=r"^\d{14}$")
    risk_index: float = Field(..., ge=0.0, le=1.0)
    signals: list[Signal]
    alert: bool


class RankingItem(BaseModel):
    """Municipal ranking item."""

    municipality: str
    uf: str
    risk_index: float = Field(..., ge=0.0, le=1.0)
    total_contracts: int
    total_value: Decimal


class RankingResponse(BaseModel):
    """Response of the /v1/ranking/municipalities endpoint."""

    period_start: date
    period_end: date
    ranking: list[RankingItem]
