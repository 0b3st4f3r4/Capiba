"""Continuous data quality monitoring.

Chunk: quality_monitor
Responsibility: Alert in real time about data quality
degradation, with configurable thresholds.

Dependencies: pandas, redis
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import redis

from capiba.config import REDIS_TTL_DEFAULT, REDIS_URL
from capiba.quality.profiling import DatasetProfile

logger = logging.getLogger(__name__)


@dataclass
class QualityAlert:
    """Quality degradation alert."""

    timestamp: str
    dataset: str
    metric: str
    current_value: float
    expected_value: float
    severity: str
    message: str


class QualityMonitor:
    """Continuous data quality monitor.

    Compares current metrics with the historical baseline
    and emits alerts when thresholds are violated.
    """

    def __init__(self) -> None:
        self.redis = redis.from_url(REDIS_URL)
        self.thresholds: dict[str, dict[str, Any]] = {
            "nulls_pct": {"max": 0.1, "severity": "warning"},
            "quality_score": {"min": 0.8, "severity": "error"},
            "duplicates_pct": {"max": 0.05, "severity": "error"},
        }

    def register_baseline(self, dataset: str, profile: DatasetProfile) -> None:
        """Registers a quality baseline in Redis.

        Args:
            dataset: Dataset identifier name.
            profile: Quality profile to be used as baseline.
        """
        key = f"capiba:quality:baseline:{dataset}"
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "quality_score": profile.quality_score,
            "columns": {
                c.name: {
                    "nulls_pct": c.nulls_pct,
                    "unique_pct": c.unique_pct,
                }
                for c in profile.columns
            },
        }
        try:
            self.redis.setex(key, REDIS_TTL_DEFAULT, json.dumps(data))
        except redis.RedisError as exc:
            # Graceful degradation: without Redis the monitor still works;
            # the baseline simply is not cached (lake holds the history).
            logger.warning(
                "Redis unavailable; baseline not cached (%s): %s", dataset, exc
            )
            return
        logger.info(
            "Baseline registered: %s (score: %.3f)", dataset, profile.quality_score
        )

    def check(self, dataset: str, profile: DatasetProfile) -> list[QualityAlert]:
        """Checks the current profile against baseline and thresholds.

        Args:
            dataset: Dataset identifier name.
            profile: Current quality profile.

        Returns:
            List of generated alerts.
        """
        alerts = []

        # Check global score
        if profile.quality_score < self.thresholds["quality_score"]["min"]:
            alerts.append(
                QualityAlert(
                    timestamp=datetime.now(UTC).isoformat(),
                    dataset=dataset,
                    metric="quality_score",
                    current_value=profile.quality_score,
                    expected_value=self.thresholds["quality_score"]["min"],
                    severity=self.thresholds["quality_score"]["severity"],
                    message=f"Quality score {profile.quality_score:.3f} below minimum {self.thresholds['quality_score']['min']}",
                )
            )

        # Check columns
        for col in profile.columns:
            if col.nulls_pct > self.thresholds["nulls_pct"]["max"]:
                alerts.append(
                    QualityAlert(
                        timestamp=datetime.now(UTC).isoformat(),
                        dataset=dataset,
                        metric=f"{col.name}:nulls_pct",
                        current_value=col.nulls_pct,
                        expected_value=self.thresholds["nulls_pct"]["max"],
                        severity=self.thresholds["nulls_pct"]["severity"],
                        message=f"Column '{col.name}': {col.nulls_pct:.1%} nulls (limit: {self.thresholds['nulls_pct']['max']:.1%})",
                    )
                )

        # Persist alerts (best effort: without Redis the alerts are still
        # returned, only the history is lost)
        if alerts:
            key = f"capiba:quality:alerts:{dataset}"
            try:
                existing = self.redis.get(key)
                history = json.loads(existing) if existing else []
                history.extend([asdict(a) for a in alerts])
                self.redis.setex(
                    key, REDIS_TTL_DEFAULT * 7, json.dumps(history[-100:])
                )
            except redis.RedisError as exc:
                logger.warning(
                    "Redis unavailable; alerts not persisted (%s): %s", dataset, exc
                )

        return alerts
