"""Ingestion data validation.

Chunk: validator
Responsibility: Checksum, schema validation,
duplicate detection and consistency.

Dependencies: hashlib
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def checksum(data: bytes) -> str:
    """Computes the SHA-256 of raw data.

    Args:
        data: Data in bytes.

    Returns:
        SHA-256 hexdigest.
    """
    return hashlib.sha256(data).hexdigest()


def detect_duplicates(
    records: list[dict[str, Any]],
    key: str = "id",
) -> list[str]:
    """Detects duplicate records by key.

    Args:
        records: List of dictionaries.
        key: Field used as the unique identifier.

    Returns:
        List of duplicated keys.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()

    for record in records:
        value = str(record.get(key, ""))
        if value in seen:
            duplicates.add(value)
        seen.add(value)

    if duplicates:
        logger.warning("Duplicates detected: %d", len(duplicates))

    return sorted(duplicates)
