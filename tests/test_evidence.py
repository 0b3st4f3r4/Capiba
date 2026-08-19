"""Tests for the multimedia evidence module.

Responsibility: Validate storage, retrieval and
integrity of evidence files.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from capiba.evidence.storage import EvidenceStorage


@pytest.fixture
def storage() -> EvidenceStorage:
    """Fixture: EvidenceStorage with mocked MinIO."""
    with patch("capiba.evidence.storage.Minio") as mock_minio:
        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_minio.return_value = mock_client
        yield EvidenceStorage()


class TestEvidenceStorage:
    """Tests for evidence storage."""

    def test_store_image(self, storage: EvidenceStorage) -> None:
        """Must store an image with valid metadata."""
        data = b"fake-image-data-jpeg"
        metadata = {
            "contract_id": "C001",
            "entity_cnpj": "12345678000195",
            "evidence_type": "contract_photo",
            "captured_at": "2026-01-15",
            "source": "on_site_inspection",
            "hash_sha256": hashlib.sha256(data).hexdigest(),
            "captured_by": "audit_agent_001",
        }

        result = storage.store(data, "photo.jpg", metadata, "image/jpeg")

        assert result["sha256"] == metadata["hash_sha256"]
        assert result["type"] == "image"
        assert result["size_bytes"] == len(data)
        assert result["bucket"] == storage.bucket
        assert result["object_name"].startswith("evidence/image/on_site_inspection/")
        storage.client.put_object.assert_called_once()

    def test_store_without_required_metadata(self, storage: EvidenceStorage) -> None:
        """Must reject a file without required metadata."""
        with pytest.raises(ValueError, match="Missing required metadata"):
            storage.store(b"data", "file.txt", {"contract_id": "C001"})

    def test_retrieve_by_hash(self, storage: EvidenceStorage) -> None:
        """Must retrieve a file by its SHA-256 hash."""
        data = b"evidence-document-pdf"
        metadata = {
            "contract_id": "C002",
            "entity_cnpj": "98765432000196",
            "evidence_type": "invoice",
            "captured_at": "2026-02-01",
            "source": "transparency_portal",
            "hash_sha256": hashlib.sha256(data).hexdigest(),
            "captured_by": "crawler_pncp",
        }

        sha256 = metadata["hash_sha256"]
        object_name = f"evidence/document/transparency_portal/2026/02/{sha256}.pdf"

        def mock_list_objects(bucket: str, **kwargs: Any) -> list[MagicMock]:
            if bucket == storage.bucket:
                mock_obj = MagicMock()
                mock_obj.object_name = object_name
                return [mock_obj]
            return []

        storage.client.list_objects.side_effect = mock_list_objects

        mock_response = MagicMock()
        mock_response.read.return_value = data
        storage.client.get_object.return_value = mock_response

        result = storage.store(data, "invoice.pdf", metadata, "application/pdf")
        recovered = storage.retrieve(result["sha256"])

        assert recovered == data
        storage.client.get_object.assert_called_once_with(storage.bucket, object_name)

    def test_classify_type_by_extension(self, storage: EvidenceStorage) -> None:
        """Must classify the type correctly by extension."""
        assert storage._classify_type("photo.jpg") == "image"
        assert storage._classify_type("doc.pdf") == "document"
        assert storage._classify_type("audio.mp3") == "audio"
        assert storage._classify_type("video.mp4") == "video"
        assert storage._classify_type("unknown.xyz") == "other"


def _metadata(data: bytes, **overrides: Any) -> dict[str, Any]:
    """Valid required metadata for a payload."""
    metadata: dict[str, Any] = {
        "contract_id": "C001",
        "entity_cnpj": "12345678000195",
        "evidence_type": "contract_photo",
        "captured_at": "2026-01-15",
        "source": "on_site_inspection",
        "hash_sha256": hashlib.sha256(data).hexdigest(),
        "captured_by": "audit_agent_001",
    }
    metadata.update(overrides)
    return metadata


class TestBucketProvisioning:
    """Tests for bucket provisioning on initialization."""

    def test_init_creates_bucket_when_missing(self) -> None:
        """Must create the bucket when it does not exist."""
        with patch("capiba.evidence.storage.Minio") as mock_minio:
            mock_client = MagicMock()
            mock_client.bucket_exists.return_value = False
            mock_minio.return_value = mock_client

            storage = EvidenceStorage()

        mock_client.make_bucket.assert_called_once_with(storage.bucket)

    def test_init_keeps_existing_bucket(self, storage: EvidenceStorage) -> None:
        """Must not recreate an existing bucket."""
        storage.client.make_bucket.assert_not_called()


class TestClassifyType:
    """Tests for the MIME-type fallback of the type classification."""

    def test_classify_by_mime_type(self, storage: EvidenceStorage) -> None:
        """Unknown extensions must fall back to the MIME type."""
        assert storage._classify_type("file.bin", "image/png") == "image"
        assert storage._classify_type("file.bin", "application/pdf") == "document"
        assert storage._classify_type("file.bin", "text/csv") == "document"
        assert storage._classify_type("file.bin", "audio/mpeg") == "audio"
        assert storage._classify_type("file.bin", "video/mp4") == "video"

    def test_classify_unknown_returns_other(self, storage: EvidenceStorage) -> None:
        """Unknown extension and MIME type must classify as other."""
        assert storage._classify_type("file.bin", "font/woff2") == "other"
        assert storage._classify_type("file.bin") == "other"


class TestValidateSize:
    """Tests for the per-type size validation."""

    def test_store_rejects_oversized_file(
        self, storage: EvidenceStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Must reject a file above the limit of its evidence type."""
        data = b"more-than-eight-bytes"
        monkeypatch.setattr("capiba.evidence.storage.EVIDENCE_MAX_SIZE_IMAGE", 8)

        with pytest.raises(ValueError, match="exceeds limit"):
            storage.store(data, "photo.jpg", _metadata(data))

        storage.client.put_object.assert_not_called()


class TestRetrieve:
    """Tests for the retrieval paths of the storage."""

    def test_retrieve_not_found_returns_none(self, storage: EvidenceStorage) -> None:
        """An unknown hash must return None."""
        storage.client.list_objects.return_value = []

        assert storage.retrieve("0" * 64) is None
        storage.client.get_object.assert_not_called()

    def test_retrieve_with_type_narrows_prefix(self, storage: EvidenceStorage) -> None:
        """An evidence type must narrow the listing prefix."""
        storage.client.list_objects.return_value = []

        assert storage.retrieve("0" * 64, evidence_type="image") is None
        _, kwargs = storage.client.list_objects.call_args
        assert kwargs["prefix"] == "evidence/image/"


class TestListByContract:
    """Tests for listing evidence linked to a contract."""

    def test_list_by_contract_filters_by_metadata(
        self, storage: EvidenceStorage
    ) -> None:
        """Must return only the objects whose metadata matches the contract."""
        match = MagicMock(object_name="evidence/image/src/2026/01/aaa.jpg", size=10)
        other = MagicMock(object_name="evidence/image/src/2026/01/bbb.jpg", size=20)
        no_meta = MagicMock(object_name="evidence/image/src/2026/01/ccc.jpg", size=30)
        storage.client.list_objects.return_value = [match, other, no_meta]

        stats = {
            match.object_name: MagicMock(
                metadata={
                    "x-amz-meta-contract-id": "C001",
                    "x-amz-meta-sha256": "aaa",
                    "x-amz-meta-evidence-type": "image",
                    "x-amz-meta-original-filename": "photo.jpg",
                    "x-amz-meta-upload-timestamp": "2026-01-15T00:00:00+00:00",
                }
            ),
            other.object_name: MagicMock(metadata={"x-amz-meta-contract-id": "C999"}),
            no_meta.object_name: MagicMock(metadata=None),
        }
        storage.client.stat_object.side_effect = lambda bucket, name: stats[name]

        results = storage.list_by_contract("C001")

        assert results == [
            {
                "sha256": "aaa",
                "bucket": storage.bucket,
                "object_name": match.object_name,
                "type": "image",
                "filename": "photo.jpg",
                "size": 10,
                "timestamp": "2026-01-15T00:00:00+00:00",
                "signal_key": None,
                "batch_sha256": None,
            }
        ]

    def test_list_by_contract_without_matches(self, storage: EvidenceStorage) -> None:
        """No matching objects must return an empty list."""
        storage.client.list_objects.return_value = []

        assert storage.list_by_contract("C404") == []


class TestSignalPackages:
    """Tests for the signal evidence packages (O9) in the storage."""

    def test_signal_key_replaces_contract_id(self, storage: EvidenceStorage) -> None:
        """Signal packages are keyed by signal_key, without contract_id."""
        data = b'{"schema": "capiba.signal-package/1"}'
        metadata = {
            "signal_key": "supplier:12345678000199:single_bid",
            "entity_cnpj": "12345678000199",
            "evidence_type": "signal_package",
            "captured_at": "2026-08-19",
            "source": "detect",
            "hash_sha256": hashlib.sha256(data).hexdigest(),
            "captured_by": "capiba-pipeline",
        }

        result = storage.store(data, "manifest.json", metadata, "application/json")

        assert result["type"] == "document"
        storage.client.put_object.assert_called_once()

    def test_signal_key_does_not_replace_other_required_metadata(
        self, storage: EvidenceStorage
    ) -> None:
        """signal_key replaces only contract_id — the rest stays required."""
        with pytest.raises(ValueError, match="Missing required metadata"):
            storage.store(
                b"data", "manifest.json", {"signal_key": "supplier:1:single_bid"}
            )

    def test_list_by_signal_filters_by_metadata(
        self, storage: EvidenceStorage
    ) -> None:
        """Must return only the packages whose metadata matches the signal."""
        key = "supplier:12345678000199:single_bid"
        match = MagicMock(object_name="evidence/document/detect/2026/08/aaa.json", size=10)
        other = MagicMock(object_name="evidence/document/detect/2026/08/bbb.json", size=20)
        storage.client.list_objects.return_value = [match, other]

        stats = {
            match.object_name: MagicMock(
                metadata={
                    "x-amz-meta-signal-key": key,
                    "x-amz-meta-batch-sha256": "batch123",
                    "x-amz-meta-sha256": "aaa",
                    "x-amz-meta-evidence-type": "document",
                    "x-amz-meta-original-filename": "manifest.json",
                    "x-amz-meta-upload-timestamp": "2026-08-19T00:00:00+00:00",
                }
            ),
            other.object_name: MagicMock(
                metadata={"x-amz-meta-signal-key": "buyer:26000:concentration"}
            ),
        }
        storage.client.stat_object.side_effect = lambda bucket, name: stats[name]

        results = storage.list_by_signal(key)

        assert len(results) == 1
        assert results[0]["signal_key"] == key
        assert results[0]["batch_sha256"] == "batch123"
        assert results[0]["sha256"] == "aaa"
