"""Filter normalized contracts by a minimum amount.

Example transformation for the declarative pipeline framework: drops
records whose ``amount`` is missing or below ``min_value``.

Usage in a YAML spec:

    transformations:
      - name: filter_by_min_value
        params:
          min_value: 1000
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def transform(
    records: list[dict[str, Any]], min_value: float = 0
) -> list[dict[str, Any]]:
    """Keeps only records with ``amount`` >= ``min_value``.

    Args:
        records: Serializable normalized contracts.
        min_value: Minimum accepted amount (inclusive).

    Returns:
        The filtered records (input order preserved).
    """
    kept: list[dict[str, Any]] = []
    for record in records:
        try:
            amount = float(record.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount >= min_value:
            kept.append(record)
    logger.info(
        "filter_by_min_value: kept %d of %d records (min_value=%s)",
        len(kept),
        len(records),
        min_value,
    )
    return kept
