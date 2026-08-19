"""Tests for the evidence API endpoints.

Responsibility: Validate upload, listing and download of evidence
with EvidenceStorage mocked via dependency override (no live MinIO).
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from capiba.api.main import app
from capiba.api.routers import evidence
from capiba.api.routers.evidence import get_storage

DATA = b"fake-evidence-pdf"
SHA256 = hashlib.sha256(DATA).hexdigest()

FORM_FIELDS = {
    "contract_id": "C001",
    "entity_cnpj": "12345678000195",
    "evidence_type": "invoice",
    "source": "transparency_portal",
    "captured_by": "crawler_pncp",
}


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client."""
    return TestClient(app)


@pytest.fixture
def storage() -> MagicMock:
    """Fixture: EvidenceStorage mock injected via dependency override."""
    mock = MagicMock()
    app.dependency_overrides[get_storage] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_storage, None)


def _stored_result() -> dict[str, Any]:
    """Return value of a successful EvidenceStorage.store call."""
    return {
        "sha256": SHA256,
        "bucket": "capiba-bronze",
        "object_name": f"evidence/document/transparency_portal/2026/02/{SHA256}.pdf",
        "type": "document",
        "size_bytes": len(DATA),
        "timestamp": "2026-02-01T00:00:00+00:00",
    }


class TestUploadEvidence:
    """Tests for POST /v1/evidence."""

    def test_upload_success(self, client: TestClient, storage: MagicMock) -> None:
        """A valid upload must store the file and return its metadata."""
        storage.store.return_value = _stored_result()

        response = client.post(
            "/v1/evidence",
            files={"file": ("invoice.pdf", DATA, "application/pdf")},
            data=FORM_FIELDS,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["sha256"] == SHA256
        assert body["type"] == "document"
        assert body["size_bytes"] == len(DATA)

        args = storage.store.call_args.args
        assert args[0] == DATA
        assert args[1] == "invoice.pdf"
        metadata = args[2]
        # Domain metadata comes from the form; the server fills the rest.
        assert metadata["contract_id"] == "C001"
        assert metadata["entity_cnpj"] == "12345678000195"
        assert metadata["hash_sha256"] == SHA256
        assert "captured_at" in metadata

    def test_upload_missing_metadata_returns_422(
        self, client: TestClient, storage: MagicMock
    ) -> None:
        """Missing required form fields must return a validation error."""
        fields = {k: v for k, v in FORM_FIELDS.items() if k != "contract_id"}

        response = client.post(
            "/v1/evidence",
            files={"file": ("invoice.pdf", DATA, "application/pdf")},
            data=fields,
        )

        assert response.status_code == 422
        storage.store.assert_not_called()

    def test_upload_invalid_metadata_returns_400(
        self, client: TestClient, storage: MagicMock
    ) -> None:
        """ValueError from the storage must return 400 with the detail."""
        storage.store.side_effect = ValueError("Missing required metadata: ['x']")

        response = client.post(
            "/v1/evidence",
            files={"file": ("invoice.pdf", DATA, "application/pdf")},
            data=FORM_FIELDS,
        )

        assert response.status_code == 400
        assert "Missing required metadata" in response.json()["detail"]

    def test_upload_storage_failure_returns_503(
        self, client: TestClient, storage: MagicMock
    ) -> None:
        """A storage failure must return 503."""
        storage.store.side_effect = ConnectionError("MinIO is down")

        response = client.post(
            "/v1/evidence",
            files={"file": ("invoice.pdf", DATA, "application/pdf")},
            data=FORM_FIELDS,
        )

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]

    def test_upload_storage_unavailable_returns_503(self, client: TestClient) -> None:
        """A storage that cannot connect must return 503."""

        def _unavailable() -> Any:
            raise HTTPException(status_code=503, detail="Evidence storage unavailable")

        app.dependency_overrides[get_storage] = _unavailable
        try:
            response = client.post(
                "/v1/evidence",
                files={"file": ("invoice.pdf", DATA, "application/pdf")},
                data=FORM_FIELDS,
            )
        finally:
            app.dependency_overrides.pop(get_storage, None)

        assert response.status_code == 503

    def test_get_storage_wraps_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_storage must convert MinIO connection failures into 503."""

        def _broken() -> Any:
            raise ConnectionError("MinIO is down")

        monkeypatch.setattr(evidence, "EvidenceStorage", _broken)

        with pytest.raises(HTTPException) as exc_info:
            get_storage()
        assert exc_info.value.status_code == 503


class TestListContractEvidence:
    """Tests for GET /v1/evidence/contract/{contract_id}."""

    def test_list_by_contract(self, client: TestClient, storage: MagicMock) -> None:
        """Must return the evidence items of the contract."""
        storage.list_by_contract.return_value = [
            {
                "sha256": SHA256,
                "bucket": "capiba-bronze",
                "object_name": f"evidence/document/src/2026/02/{SHA256}.pdf",
                "type": "document",
                "filename": "invoice.pdf",
                "size": len(DATA),
                "timestamp": "2026-02-01T00:00:00+00:00",
            }
        ]

        response = client.get("/v1/evidence/contract/C001")

        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["sha256"] == SHA256
        assert items[0]["filename"] == "invoice.pdf"
        storage.list_by_contract.assert_called_once_with("C001")

    def test_list_without_matches(self, client: TestClient, storage: MagicMock) -> None:
        """A contract without evidence must return an empty list."""
        storage.list_by_contract.return_value = []

        response = client.get("/v1/evidence/contract/C404")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_storage_failure_returns_503(
        self, client: TestClient, storage: MagicMock
    ) -> None:
        """A storage failure must return 503."""
        storage.list_by_contract.side_effect = ConnectionError("MinIO is down")

        response = client.get("/v1/evidence/contract/C001")

        assert response.status_code == 503


class TestDownloadEvidence:
    """Tests for GET /v1/evidence/{sha256}."""

    def test_download_found(self, client: TestClient, storage: MagicMock) -> None:
        """An existing hash must return the file bytes."""
        storage.retrieve.return_value = DATA

        response = client.get(f"/v1/evidence/{SHA256}")

        assert response.status_code == 200
        assert response.content == DATA
        storage.retrieve.assert_called_once_with(SHA256)

    def test_download_not_found(self, client: TestClient, storage: MagicMock) -> None:
        """An unknown hash must return 404."""
        storage.retrieve.return_value = None

        response = client.get(f"/v1/evidence/{SHA256}")

        assert response.status_code == 404

    def test_download_storage_failure_returns_503(
        self, client: TestClient, storage: MagicMock
    ) -> None:
        """A storage failure must return 503."""
        storage.retrieve.side_effect = ConnectionError("MinIO is down")

        response = client.get(f"/v1/evidence/{SHA256}")

        assert response.status_code == 503
