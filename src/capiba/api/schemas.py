"""API Pydantic schemas.

Responsibility: typed request/response models
for all endpoints of the signals API.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from capiba.detection.signals import SignalType

__all__ = [
    "EvidenceItem",
    "EvidenceStored",
    "RankingItem",
    "RankingResponse",
    "Signal",
    "SignalType",
    "SignalsResponse",
]


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


class EvidenceStored(BaseModel):
    """Response of the POST /v1/evidence endpoint (EvidenceStorage.store)."""

    sha256: str
    bucket: str
    object_name: str
    type: str
    size_bytes: int
    timestamp: str


class EvidenceItem(BaseModel):
    """Item of the GET /v1/evidence/contract/{contract_id} listing."""

    sha256: str | None = None
    bucket: str
    object_name: str | None = None
    type: str | None = None
    filename: str | None = None
    size: int | None = None
    timestamp: str | None = None
