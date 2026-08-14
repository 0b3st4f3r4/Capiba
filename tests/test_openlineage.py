"""Tests for the Capiba OpenLineage asset converter.

These tests run offline by mocking Airflow's provider manager so that the
``capiba://`` scheme can be exercised without a running Airflow instance.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.pipeline.openlineage import (
    _convert_capiba_asset,
    _normalize_capiba_uri,
    register_capiba_openlineage,
)


class FakeAsset:
    """Minimal stand-in for ``airflow.sdk.definitions.asset.Asset``."""

    def __init__(self, uri: str) -> None:
        self.uri = uri


@pytest.fixture
def fake_manager() -> MagicMock:
    """Provider manager with mutable handler/converter dicts."""
    manager = MagicMock()
    manager._asset_uri_handlers = {}
    manager._asset_to_openlineage_converters = {}
    return manager


def test_normalize_capiba_uri_is_identity() -> None:
    """The normalizer returns the URI unchanged."""
    uri = "capiba://bronze/raw_pncp"
    assert _normalize_capiba_uri(uri) is uri


def test_convert_capiba_asset_parses_host_and_path() -> None:
    """Converter extracts namespace ``capiba`` and host+path as name."""
    asset = FakeAsset("capiba://bronze/raw_pncp")
    dataset = _convert_capiba_asset(asset, None)

    assert dataset.namespace == "capiba"
    assert dataset.name == "bronze/raw_pncp"


def test_convert_capiba_asset_without_host() -> None:
    """URIs with only a path still produce a valid name."""
    asset = FakeAsset("capiba://contracts")
    dataset = _convert_capiba_asset(asset, None)

    assert dataset.namespace == "capiba"
    assert dataset.name == "contracts"


def test_register_capiba_openlineage_populates_manager(fake_manager: Any) -> None:
    """Registration adds the handler and converter for the ``capiba`` scheme."""
    from capiba.pipeline import openlineage as ol_mod

    ol_mod._register_provider_manager(fake_manager)

    assert "capiba" in fake_manager._asset_uri_handlers
    assert "capiba" in fake_manager._asset_to_openlineage_converters
    assert fake_manager._asset_uri_handlers["capiba"] is _normalize_capiba_uri
    assert fake_manager._asset_to_openlineage_converters["capiba"] is _convert_capiba_asset


def test_register_capiba_openlineage_is_idempotent(fake_manager: Any) -> None:
    """Calling registration twice does not replace the existing entries."""
    from capiba.pipeline import openlineage as ol_mod

    ol_mod._register_provider_manager(fake_manager)
    first_handler = fake_manager._asset_uri_handlers["capiba"]
    first_converter = fake_manager._asset_to_openlineage_converters["capiba"]

    ol_mod._register_provider_manager(fake_manager)

    assert fake_manager._asset_uri_handlers["capiba"] is first_handler
    assert fake_manager._asset_to_openlineage_converters["capiba"] is first_converter


def test_register_capiba_openlineage_runs_without_airflow(monkeypatch: Any) -> None:
    """The top-level registration swallows provider-manager import failures."""
    from capiba.pipeline import openlineage as ol_mod

    def _broken_classes() -> list[type[Any]]:
        class Broken:
            def __init__(self) -> None:
                raise RuntimeError("not available")

        return [Broken]

    monkeypatch.setattr(ol_mod, "_provider_manager_classes", _broken_classes)

    # Must not raise even though every provider manager fails to instantiate.
    register_capiba_openlineage()
