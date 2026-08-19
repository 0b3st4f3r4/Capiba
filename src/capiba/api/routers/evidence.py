"""Evidence endpoints.

Chunk: evidence
Responsibility: Upload, list and download multimedia evidence
linked to contracts/entities, backed by EvidenceStorage (MinIO).

Dependencies: fastapi, capiba.evidence.storage
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from capiba.api.schemas import EvidenceItem, EvidenceStored
from capiba.evidence.storage import EvidenceStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])

_HTTP_503_DETAIL = "Evidence storage unavailable"


def get_storage() -> EvidenceStorage:
    """Instantiates the evidence storage lazily (connects to MinIO).

    Deferred to request time so the app imports and starts offline;
    tests override this dependency with a mock.

    Raises:
        HTTPException 503: If the storage is unavailable.
    """
    try:
        return EvidenceStorage()
    except Exception as exc:
        logger.warning("Evidence storage unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc


@router.post("", response_model=EvidenceStored, status_code=201)
async def upload_evidence(
    file: Annotated[UploadFile, File()],
    contract_id: Annotated[str, Form()],
    entity_cnpj: Annotated[str, Form()],
    evidence_type: Annotated[str, Form()],
    source: Annotated[str, Form()],
    captured_by: Annotated[str, Form()],
    storage: Annotated[EvidenceStorage, Depends(get_storage)],
) -> EvidenceStored:
    """Stores an evidence file with the required domain metadata.

    ``captured_at`` and ``hash_sha256`` are filled in by the server
    (the hash is computed over the uploaded bytes).

    Raises:
        HTTPException 400: If the metadata is invalid or the file exceeds
            the size limit of its evidence type.
        HTTPException 503: If the storage is unavailable.
    """
    data = await file.read()
    metadata = {
        "contract_id": contract_id,
        "entity_cnpj": entity_cnpj,
        "evidence_type": evidence_type,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": source,
        "hash_sha256": hashlib.sha256(data).hexdigest(),
        "captured_by": captured_by,
    }
    try:
        result = storage.store(
            data, file.filename or "evidence.bin", metadata, file.content_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Evidence store failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return EvidenceStored(**result)


@router.get("/contract/{contract_id}", response_model=list[EvidenceItem])
async def list_contract_evidence(
    contract_id: str,
    storage: Annotated[EvidenceStorage, Depends(get_storage)],
) -> list[EvidenceItem]:
    """Lists all evidence linked to a contract.

    Raises:
        HTTPException 503: If the storage is unavailable.
    """
    try:
        items = storage.list_by_contract(contract_id)
    except Exception as exc:
        logger.warning("Evidence listing failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return [EvidenceItem(**item) for item in items]


@router.get("/{sha256}")
async def download_evidence(
    sha256: str,
    storage: Annotated[EvidenceStorage, Depends(get_storage)],
) -> Response:
    """Downloads an evidence file by its SHA-256 hash.

    Raises:
        HTTPException 404: If no evidence matches the hash.
        HTTPException 503: If the storage is unavailable.
    """
    try:
        data = storage.retrieve(sha256)
    except Exception as exc:
        logger.warning("Evidence retrieval failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return Response(content=data, media_type="application/octet-stream")
