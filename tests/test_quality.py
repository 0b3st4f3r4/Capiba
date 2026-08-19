"""Tests for the data quality module.

Responsibility: Validate profiling, validators, monitor
and the lineage tracker.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import redis

from capiba.quality.lineage import LineageTracker
from capiba.quality.monitor import QualityMonitor
from capiba.quality.profiling import (
    ColumnProfile,
    DatasetProfile,
    profile_column,
    profile_dataset,
)
from capiba.quality.validators import CONTRACT_RULES, QualityValidator


class TestProfiling:
    """Tests for data profiling."""

    def test_profile_column_numeric(self) -> None:
        """Must generate a complete profile for a numeric column."""
        series = pd.Series([1, 2, 3, 4, 5, None, 100])
        profile = profile_column(series, "amount")

        assert profile.name == "amount"
        assert profile.type == "float64"
        assert profile.total_records == 7
        assert profile.nulls == 1
        assert profile.nulls_pct == pytest.approx(0.1429, abs=0.001)
        assert profile.min == 1.0
        assert profile.max == 100.0
        assert profile.mean == pytest.approx(19.17, abs=0.01)

    def test_profile_column_categorical(self) -> None:
        """Must generate a profile for a categorical column."""
        series = pd.Series(["A", "B", "A", "C", "A"])
        profile = profile_column(series, "modality")

        assert profile.dominant_pattern == "A"
        assert profile.dominant_pattern_freq == 3

    def test_profile_dataset_with_alerts(self) -> None:
        """Must detect anomalies and generate alerts."""
        df = pd.DataFrame(
            {
                "id": ["C001", "C002", "C003"],
                "amount": [1000, None, None],  # 66% nulls
                "modality": ["pregao", "pregao", "pregao"],
            }
        )

        profile = profile_dataset(df, "test_contracts")
        assert profile.quality_score < 1.0
        assert len(profile.alerts) > 0
        assert any("nulls" in a for a in profile.alerts)


class TestValidators:
    """Tests for rule validators."""

    def test_validator_contract_rules(self) -> None:
        """Must apply the pre-defined procurement rules."""
        df = pd.DataFrame(
            {
                "amount": [1000, -500, 2000],  # -500 violates positive_value
                "supplier_cnpj": [
                    "12345678000195",
                    "123",
                    "98765432000196",
                ],  # "123" violates
                "signature_date": pd.to_datetime(
                    ["2026-01-01", "2026-02-01", None]
                ),  # None violates
                "subject": [
                    "Material de escritório",
                    "A",
                    "Serviços de limpeza",
                ],  # "A" violates
            }
        )

        validator = QualityValidator()
        for rule in CONTRACT_RULES:
            validator.add_rule(rule)

        results = validator.validate(df)

        assert len(results) > 0
        # Check that violations occurred
        violations = [r for r in results if r.violations > 0]
        assert len(violations) > 0

    def test_validator_missing_column(self) -> None:
        """Must ignore a rule if the column does not exist."""
        df = pd.DataFrame({"other_column": [1, 2, 3]})

        validator = QualityValidator()
        validator.add_rule(CONTRACT_RULES[0])

        results = validator.validate(df)
        assert len(results) == 0  # Ignored due to missing column

    def test_validator_sample_is_json_serializable(self) -> None:
        """Violation samples containing NaN must be serialized as None."""
        df = pd.DataFrame({"amount": [1000.0, float("nan"), -200.0]})

        validator = QualityValidator()
        validator.add_rule(CONTRACT_RULES[0])  # positive_value
        validator.add_rule(CONTRACT_RULES[4])  # amount_not_extreme

        results = validator.validate(df)

        for result in results:
            sample = result.violation_sample
            assert all(not (isinstance(v, float) and pd.isna(v)) for v in sample)
            # Must be JSON-serializable
            json.dumps(sample)


class TestLineage:
    """Tests for data traceability."""

    def test_register_source(self) -> None:
        """Must register a source and return an ID."""
        tracker = LineageTracker()
        source_id = tracker.register_source(
            name="PNCP",
            url="https://pncp.gov.br/api",
            api_type="REST",
        )
        assert source_id in tracker.nodes
        assert tracker.nodes[source_id].type == "source"

    def test_register_transformation(self) -> None:
        """Must create dependency edges."""
        tracker = LineageTracker()

        source_id = tracker.register_source("PNCP", "https://pncp.gov.br", "REST")
        transform_id = tracker.register_transformation(
            name="normalize_pncp",
            input_ids=[source_id],
            code_hash="abc123",
        )

        assert len(tracker.edges) == 1
        assert tracker.edges[0].source == source_id
        assert tracker.edges[0].target == transform_id

    def test_export(self) -> None:
        """Must export the graph as a serializable dict."""
        tracker = LineageTracker()
        tracker.register_source("PNCP", "https://pncp.gov.br", "REST")

        export = tracker.export()
        assert "nodes" in export
        assert "edges" in export
        assert len(export["nodes"]) == 1


def _profile(
    quality_score: float = 0.95,
    columns: list[ColumnProfile] | None = None,
) -> DatasetProfile:
    """Builds a DatasetProfile with the given score and columns."""
    return DatasetProfile(
        name="contracts",
        total_records=10,
        total_columns=1,
        columns=columns
        if columns is not None
        else [
            ColumnProfile(
                name="amount",
                type="float64",
                total_records=10,
                nulls=0,
                nulls_pct=0.0,
                unique_count=10,
                unique_pct=1.0,
            )
        ],
        quality_score=quality_score,
        alerts=[],
    )


class TestMonitor:
    """Tests for the continuous quality monitor (Redis mocked)."""

    @pytest.fixture
    def monitor(self) -> QualityMonitor:
        """Returns a QualityMonitor with a mocked Redis client."""
        with patch("capiba.quality.monitor.redis.from_url") as mock_from_url:
            mock_from_url.return_value = MagicMock()
            return QualityMonitor()

    def test_register_baseline(self, monitor: QualityMonitor) -> None:
        """Must store the baseline as JSON with the expected key and TTL."""
        monitor.register_baseline("contracts", _profile(quality_score=0.9))

        monitor.redis.setex.assert_called_once()
        key, ttl, payload = monitor.redis.setex.call_args[0]
        assert key == "capiba:quality:baseline:contracts"
        assert ttl > 0
        data = json.loads(payload)
        assert data["quality_score"] == 0.9
        assert "timestamp" in data
        assert data["columns"]["amount"]["nulls_pct"] == 0.0
        assert data["columns"]["amount"]["unique_pct"] == 1.0

    def test_check_clean_profile_no_alerts(self, monitor: QualityMonitor) -> None:
        """A profile within thresholds must not emit or persist alerts."""
        alerts = monitor.check("contracts", _profile())

        assert alerts == []
        monitor.redis.setex.assert_not_called()
        monitor.redis.get.assert_not_called()

    def test_check_low_quality_score_alert(self, monitor: QualityMonitor) -> None:
        """A score below the minimum must emit an error alert."""
        monitor.redis.get.return_value = None

        alerts = monitor.check("contracts", _profile(quality_score=0.5))

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.dataset == "contracts"
        assert alert.metric == "quality_score"
        assert alert.current_value == 0.5
        assert alert.expected_value == 0.8
        assert alert.severity == "error"
        # Alert history must be persisted in Redis
        monitor.redis.setex.assert_called_once()
        key, _, payload = monitor.redis.setex.call_args[0]
        assert key == "capiba:quality:alerts:contracts"
        history = json.loads(payload)
        assert len(history) == 1
        assert history[0]["metric"] == "quality_score"

    def test_check_nulls_threshold_alert(self, monitor: QualityMonitor) -> None:
        """A column above the nulls threshold must emit a warning alert."""
        monitor.redis.get.return_value = None
        column = ColumnProfile(
            name="subject",
            type="object",
            total_records=10,
            nulls=5,
            nulls_pct=0.5,
            unique_count=5,
            unique_pct=0.5,
        )

        alerts = monitor.check("contracts", _profile(columns=[column]))

        assert len(alerts) == 1
        assert alerts[0].metric == "subject:nulls_pct"
        assert alerts[0].severity == "warning"
        assert alerts[0].current_value == 0.5

    def test_check_appends_to_existing_history(self, monitor: QualityMonitor) -> None:
        """New alerts must extend the history already stored in Redis."""
        existing = [{"metric": "quality_score", "dataset": "contracts"}]
        monitor.redis.get.return_value = json.dumps(existing)

        alerts = monitor.check("contracts", _profile(quality_score=0.5))

        assert len(alerts) == 1
        _, _, payload = monitor.redis.setex.call_args[0]
        history = json.loads(payload)
        assert len(history) == 2
        assert history[0] == existing[0]

    def test_register_baseline_degrades_without_redis(
        self, monitor: QualityMonitor
    ) -> None:
        """A Redis failure must not break the baseline registration."""
        monitor.redis.setex.side_effect = redis.ConnectionError("boom")

        monitor.register_baseline("contracts", _profile(quality_score=0.9))

    def test_check_degrades_without_redis(self, monitor: QualityMonitor) -> None:
        """A Redis failure must not lose the computed alerts."""
        monitor.redis.get.side_effect = redis.ConnectionError("boom")
        monitor.redis.setex.side_effect = redis.ConnectionError("boom")

        alerts = monitor.check("contracts", _profile(quality_score=0.5))

        assert len(alerts) == 1
        assert alerts[0].metric == "quality_score"

    def test_record_batch_appends_metrics(self, monitor: QualityMonitor) -> None:
        """Batch metrics must be appended to the dataset history."""
        monitor.redis.get.return_value = json.dumps(
            [{"timestamp": "2026-08-17T00:00:00+00:00", "total": 5}]
        )

        monitor.record_batch("pipeline:daily", {"total": 10, "duplicates": 1})

        key, ttl, payload = monitor.redis.setex.call_args[0]
        assert key == "capiba:quality:metrics:pipeline:daily"
        assert ttl > 0
        history = json.loads(payload)
        assert len(history) == 2
        assert history[1]["total"] == 10
        assert "timestamp" in history[1]

    def test_record_batch_degrades_without_redis(
        self, monitor: QualityMonitor
    ) -> None:
        """A Redis failure must not break the batch recording."""
        monitor.redis.get.side_effect = redis.ConnectionError("boom")

        monitor.record_batch("pipeline:daily", {"total": 10})

    def test_get_metrics_filters_by_since(self, monitor: QualityMonitor) -> None:
        """Only entries at/after ``since`` must be returned."""
        monitor.redis.get.return_value = json.dumps(
            [
                {"timestamp": "2026-08-10T00:00:00+00:00", "total": 1},
                {"timestamp": "2026-08-18T00:00:00+00:00", "total": 2},
            ]
        )

        entries = monitor.get_metrics(
            "pipeline:daily", since=datetime(2026, 8, 15, tzinfo=UTC)
        )

        assert entries == [
            {"timestamp": "2026-08-18T00:00:00+00:00", "total": 2}
        ]

    def test_get_metrics_empty_without_redis(self, monitor: QualityMonitor) -> None:
        """A Redis failure must yield an empty history."""
        monitor.redis.get.side_effect = redis.ConnectionError("boom")

        assert monitor.get_metrics("pipeline:daily") == []

    def test_list_datasets(self, monitor: QualityMonitor) -> None:
        """Datasets must come from the metrics keys, without the prefix."""
        monitor.redis.scan_iter.return_value = iter(
            [
                "capiba:quality:metrics:pipeline:daily_ingestion",
                "capiba:quality:metrics:pipeline:hourly_pod_usage",
            ]
        )

        assert monitor.list_datasets() == [
            "pipeline:daily_ingestion",
            "pipeline:hourly_pod_usage",
        ]

    def test_list_datasets_empty_without_redis(
        self, monitor: QualityMonitor
    ) -> None:
        """A Redis failure must yield an empty dataset list."""
        monitor.redis.scan_iter.side_effect = redis.ConnectionError("boom")

        assert monitor.list_datasets() == []
