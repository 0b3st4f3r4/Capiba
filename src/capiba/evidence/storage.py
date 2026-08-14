"""Multimedia evidence storage.

Chunk: evidence_storage
Responsibility: Store and retrieve evidence files
(image, document, audio, video) in the MinIO bronze bucket,
under prefixes segmented by format and origin
(evidence/<type>/<source>/), with a SHA-256 hash as the
integrity identifier and required metadata linked to the
contract/entity.

Dependencies: minio, hashlib
"""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from minio import Minio

from capiba.config import (
    EVIDENCE_BUCKET,
    EVIDENCE_FORMATS_AUDIO,
    EVIDENCE_FORMATS_DOCUMENT,
    EVIDENCE_FORMATS_IMAGE,
    EVIDENCE_FORMATS_VIDEO,
    EVIDENCE_MAX_SIZE_AUDIO,
    EVIDENCE_MAX_SIZE_DOCUMENT,
    EVIDENCE_MAX_SIZE_IMAGE,
    EVIDENCE_MAX_SIZE_VIDEO,
    EVIDENCE_MINIO_ACCESS_KEY,
    EVIDENCE_MINIO_ENDPOINT,
    EVIDENCE_MINIO_SECRET_KEY,
    EVIDENCE_MINIO_SECURE,
    EVIDENCE_REQUIRED_METADATA,
)

logger = logging.getLogger(__name__)

EVIDENCE_PREFIX = "evidence"


class EvidenceStorage:
    """Evidence storage in MinIO with SHA-256 integrity.

    Every evidence file is stored with:
    - SHA-256 hash as the integrity identifier
    - Required metadata linked to the contract/entity
    - Size validation per evidence type
    - Object keys segmented by format and origin:
      evidence/<type>/<source>/<year>/<month>/<sha256>.<ext>
    """

    def __init__(self) -> None:
        self.client = Minio(
            EVIDENCE_MINIO_ENDPOINT,
            access_key=EVIDENCE_MINIO_ACCESS_KEY,
            secret_key=EVIDENCE_MINIO_SECRET_KEY,
            secure=EVIDENCE_MINIO_SECURE,
        )
        self.bucket = EVIDENCE_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Ensures the evidence bucket exists."""
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info("Bucket created: %s", self.bucket)

    def _classify_type(self, filename: str, content_type: str | None = None) -> str:
        """Classifies the file into an evidence type.

        Args:
            filename: File name.
            content_type: MIME type (optional).

        Returns:
            Type: image, document, audio, video, or other.
        """
        ext = Path(filename).suffix.lower().lstrip(".")

        if ext in EVIDENCE_FORMATS_IMAGE:
            return "image"
        if ext in EVIDENCE_FORMATS_DOCUMENT:
            return "document"
        if ext in EVIDENCE_FORMATS_AUDIO:
            return "audio"
        if ext in EVIDENCE_FORMATS_VIDEO:
            return "video"

        # Fallback by MIME type
        if content_type:
            if content_type.startswith("image/"):
                return "image"
            if content_type.startswith("application/") or content_type.startswith(
                "text/"
            ):
                return "document"
            if content_type.startswith("audio/"):
                return "audio"
            if content_type.startswith("video/"):
                return "video"

        return "other"

    def _object_prefix(self, evidence_type: str, source: str) -> str:
        """Builds the object key prefix for a type/origin pair.

        Args:
            evidence_type: Evidence type (image, document, audio, ...).
            source: Evidence origin (from the required metadata).

        Returns:
            Prefix evidence/<type>/<source>/ with a slugged source.
        """
        slug = re.sub(r"[^a-z0-9._-]+", "-", source.strip().lower()).strip("-")
        return f"{EVIDENCE_PREFIX}/{evidence_type}/{slug or 'unknown'}"

    def _validate_metadata(self, metadata: dict[str, Any]) -> None:
        """Validates presence of required metadata.

        Args:
            metadata: Dict with the file's metadata.

        Raises:
            ValueError: If required metadata is missing.
        """
        missing = [k for k in EVIDENCE_REQUIRED_METADATA if k not in metadata]
        if missing:
            raise ValueError(f"Missing required metadata: {missing}")

    def _validate_size(self, data: bytes, evidence_type: str) -> None:
        """Validates file size against per-type limits.

        Args:
            data: File contents in bytes.
            evidence_type: Evidence type.

        Raises:
            ValueError: If the file exceeds the limit.
        """
        size = len(data)
        limits = {
            "image": EVIDENCE_MAX_SIZE_IMAGE,
            "document": EVIDENCE_MAX_SIZE_DOCUMENT,
            "audio": EVIDENCE_MAX_SIZE_AUDIO,
            "video": EVIDENCE_MAX_SIZE_VIDEO,
        }
        limit = limits.get(evidence_type, EVIDENCE_MAX_SIZE_DOCUMENT)

        if size > limit:
            raise ValueError(
                f"File exceeds limit of {limit} bytes for type {evidence_type}"
            )

    def store(
        self,
        data: bytes,
        filename: str,
        metadata: dict[str, Any],
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Stores an evidence file with integrity guarantees.

        Args:
            data: File contents in bytes.
            filename: Original file name.
            metadata: Required metadata (contract_id, entity_cnpj, etc.).
            content_type: MIME type (optional).

        Returns:
            Dict with hash, bucket, object_name and access URL.
        """
        # Validate
        self._validate_metadata(metadata)
        evidence_type = self._classify_type(filename, content_type)
        self._validate_size(data, evidence_type)

        # Compute SHA-256 hash
        sha256 = hashlib.sha256(data).hexdigest()

        # Build hierarchical path: evidence/<type>/<source>/<year>/<month>/<hash>.<ext>
        now = datetime.now(UTC)
        ext = Path(filename).suffix
        prefix = self._object_prefix(evidence_type, str(metadata["source"]))
        object_name = f"{prefix}/{now.year}/{now.month:02d}/{sha256}{ext}"

        # Prepare object metadata
        object_metadata: dict[str, str | list[str] | tuple[str]] = {
            "x-amz-meta-original-filename": filename,
            "x-amz-meta-upload-timestamp": now.isoformat(),
            "x-amz-meta-sha256": sha256,
            "x-amz-meta-evidence-type": evidence_type,
            **{f"x-amz-meta-{k}": str(v) for k, v in metadata.items()},
        }

        # Upload
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(data),
            len(data),
            # minio falls back to a default content type internally
            content_type=cast(str, content_type or mimetypes.guess_type(filename)[0]),
            metadata=object_metadata,
        )

        logger.info(
            "Evidence stored: %s/%s (SHA-256: %s)", self.bucket, object_name, sha256
        )

        return {
            "sha256": sha256,
            "bucket": self.bucket,
            "object_name": object_name,
            "type": evidence_type,
            "size_bytes": len(data),
            "timestamp": now.isoformat(),
        }

    def retrieve(self, sha256: str, evidence_type: str | None = None) -> bytes | None:
        """Retrieves evidence by its SHA-256 hash.

        Args:
            sha256: File hash.
            evidence_type: Evidence type (optional, speeds up the search).

        Returns:
            File contents in bytes, or None if not found.
        """
        prefix = (
            f"{EVIDENCE_PREFIX}/{evidence_type}/" if evidence_type else EVIDENCE_PREFIX
        )

        objects = self.client.list_objects(self.bucket, prefix=prefix, recursive=True)
        for obj in objects:
            object_name = obj.object_name
            if object_name is None:
                continue
            if sha256 in object_name:
                response = self.client.get_object(self.bucket, object_name)
                return response.read()

        logger.warning("Evidence not found: SHA-256 %s", sha256)
        return None

    def list_by_contract(self, contract_id: str) -> list[dict[str, Any]]:
        """Lists all evidence linked to a contract.

        Args:
            contract_id: Contract identifier.

        Returns:
            List of evidence metadata.
        """
        results = []

        objects = self.client.list_objects(
            self.bucket, prefix=EVIDENCE_PREFIX, recursive=True
        )
        for obj in objects:
            object_name = obj.object_name
            if object_name is None:
                continue
            stat = self.client.stat_object(self.bucket, object_name)
            meta = stat.metadata or {}
            if meta.get("x-amz-meta-contract-id") == contract_id:
                results.append(
                    {
                        "sha256": meta.get("x-amz-meta-sha256"),
                        "bucket": self.bucket,
                        "object_name": obj.object_name,
                        "type": meta.get("x-amz-meta-evidence-type"),
                        "filename": meta.get("x-amz-meta-original-filename"),
                        "size": obj.size,
                        "timestamp": meta.get("x-amz-meta-upload-timestamp"),
                    }
                )

        return results
