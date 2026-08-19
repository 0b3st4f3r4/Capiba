"""Tests for the centralized configuration module.

Responsibility: Validate key defaults and the parsing of list/boolean
environment variables in ``capiba.config``.
"""

from __future__ import annotations

import importlib

import pytest

from capiba import config


class TestDefaults:
    """Key defaults must hold without environment overrides."""

    def test_object_storage_defaults(self) -> None:
        assert config.MINIO_ENDPOINT == "localhost:9000"
        assert config.MINIO_SECURE is False

    def test_lake_bucket_defaults(self) -> None:
        assert config.LAKE_BUCKET_BRONZE == "capiba-bronze"
        assert config.LAKE_BUCKET_SILVER == "capiba-silver"
        assert config.LAKE_BUCKET_GOLD == "capiba-gold"

    def test_redis_defaults(self) -> None:
        assert config.REDIS_URL == "redis://localhost:6379/0"
        assert config.REDIS_TTL_DEFAULT == 3600

    def test_notification_defaults(self) -> None:
        """Without env, notifications are disabled and the threshold is 0.7."""
        assert config.NOTIFICATION_RECIPIENTS == []
        assert pytest.approx(0.7) == config.NOTIFICATION_ALERT_SCORE

    def test_evidence_required_metadata(self) -> None:
        assert "contract_id" in config.EVIDENCE_REQUIRED_METADATA
        assert "hash_sha256" in config.EVIDENCE_REQUIRED_METADATA


class TestEnvParsing:
    """Environment variables must be parsed into typed values."""

    def test_comma_separated_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTIFICATION_RECIPIENTS", " a@x.org ,b@x.org,,")
        importlib.reload(config)
        assert config.NOTIFICATION_RECIPIENTS == ["a@x.org", "b@x.org"]

    def test_boolean_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIO_SECURE", "true")
        importlib.reload(config)
        assert config.MINIO_SECURE is True

    def test_integer_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_TTL_DEFAULT", "60")
        importlib.reload(config)
        assert config.REDIS_TTL_DEFAULT == 60

    @pytest.fixture(autouse=True)
    def _restore_config(self) -> None:
        """Reloads the module after each test to undo env overrides."""
        yield
        importlib.reload(config)
