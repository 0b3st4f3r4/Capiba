"""API Pydantic schemas.

Responsibility: typed request/response models
for all endpoints of the signals API.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from capiba.db.triage import TriageStatus
from capiba.detection.signals import SignalType

__all__ = [
    "EvidenceItem",
    "EvidenceStored",
    "FtmExportResponse",
    "OwnershipResponse",
    "PartnerOfBuyer",
    "PartnersResponse",
    "RankingItem",
    "RankingResponse",
    "Signal",
    "SignalReview",
    "SignalType",
    "SignalsResponse",
    "TriageMetrics",
    "TriageRequest",
    "TriageStatus",
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
    signal_key: str | None = None
    batch_sha256: str | None = None


class OwnershipResponse(BaseModel):
    """Response of the GET /v1/graph/ownership/{cnpj} endpoint."""

    entity: str = Field(..., pattern=r"^\d{14}$")
    max_depth: int
    paths: list[list[str]]


class PartnerOfBuyer(BaseModel):
    """One partner row of GET /v1/graph/partners/{siafi_code}."""

    supplier_cnpj: str
    company: str
    edge: str
    partner_key: str
    partner_schema: str | None = None
    partner_name: str | None = None


class PartnersResponse(BaseModel):
    """Response of the GET /v1/graph/partners/{siafi_code} endpoint."""

    siafi_code: str
    partners: list[PartnerOfBuyer]


class FtmExportResponse(BaseModel):
    """Response of the GET /v1/graph/ftm/{cnpj} endpoint."""

    entity: str = Field(..., pattern=r"^\d{14}$")
    entities: list[dict[str, Any]]


class SignalReview(BaseModel):
    """Triage entry of a detected signal (GET /v1/triage/signals)."""

    key: str
    entity_type: str
    entity_id: str
    signal_type: str
    score: float | None = None
    details: str | None = None
    status: TriageStatus
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    reason: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None


class TriageRequest(BaseModel):
    """Request of the POST /v1/triage/signals/{key}/review endpoint."""

    status: TriageStatus = Field(..., description="Target editorial state")
    reviewer: str = Field(..., min_length=1)
    reason: str | None = Field(
        default=None, description="Mandatory when status is rejected"
    )


class TriageMetrics(BaseModel):
    """Per-operator precision report (GET /v1/triage/metrics)."""

    signal_type: str
    pending_review: int
    confirmed: int
    rejected: int
    published: int
    precision: float | None = None
