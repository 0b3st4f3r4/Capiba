"""Tests for the public read-only API (O11).

Responsibility: Validate the public mart listing, the presigned-download
redirect and the methodology endpoint, with the MinIO storage faked via
dependency override (no live infra).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from capiba.api.main import app
from capiba.api.routers import public as public_router
from capiba.config import PUBLIC_EXPORT_BUCKET


@dataclass
class _FakeObject:
    object_name: str
    size: int = 10


class _FakePublicStorage:
    """In-memory stand-in for the public MinIO bucket."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.presigned: list[tuple[str, str]] = []

    def list_objects(
        self, bucket: str, prefix: str = "", recursive: bool = False
    ) -> list[_FakeObject]:
        return [_FakeObject(key) for key in self.keys if key.startswith(prefix)]

    def presigned_get_object(self, bucket: str, key: str, expires: Any = None) -> str:
        self.presigned.append((bucket, key))
        return f"https://minio.example/{bucket}/{key}?sig=fake"


_KEYS = [
    "marts/contracts_daily/dt=2026-08-19/contracts_daily.csv",
    "marts/contracts_daily/dt=2026-08-19/contracts_daily.parquet",
    "marts/contracts_daily/dt=2026-08-19/manifest.json",
    "marts/contracts_daily/dt=2026-08-20/contracts_daily.csv",
    "marts/contracts_daily/dt=2026-08-20/contracts_daily.parquet",
    "marts/supplier_stats/dt=2026-08-20/supplier_stats.csv",
    "marts/supplier_stats/dt=2026-08-20/manifest.json",
]


@pytest.fixture
def client() -> TestClient:
    """API test client with the public storage faked."""
    fake = _FakePublicStorage(list(_KEYS))
    app.dependency_overrides[public_router.get_public_storage] = lambda: fake
    test_client = TestClient(app)
    test_client.fake_storage = fake  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


class TestListMarts:
    def test_lists_exported_marts_and_dates(self, client: TestClient) -> None:
        response = client.get("/v1/public/marts")

        assert response.status_code == 200
        body = response.json()
        assert body["bucket"] == PUBLIC_EXPORT_BUCKET
        marts = {m["name"]: m for m in body["marts"]}
        assert marts["contracts_daily"]["dates"] == ["2026-08-20", "2026-08-19"]
        assert marts["supplier_stats"]["dates"] == ["2026-08-20"]
        assert marts["contracts_daily"]["lgpd_classification"]

    def test_storage_failure_is_503(self, client: TestClient) -> None:
        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("minio down")

        client.fake_storage.list_objects = broken  # type: ignore[attr-defined]
        response = client.get("/v1/public/marts")
        assert response.status_code == 503


class TestDownloadMart:
    def test_redirects_to_presigned_latest(self, client: TestClient) -> None:
        response = client.get(
            "/v1/public/marts/contracts_daily/parquet", follow_redirects=False
        )

        assert response.status_code == 302
        assert (
            response.headers["location"]
            == f"https://minio.example/{PUBLIC_EXPORT_BUCKET}/marts/contracts_daily/"
            "dt=2026-08-20/contracts_daily.parquet?sig=fake"
        )

    def test_pinned_date(self, client: TestClient) -> None:
        response = client.get(
            "/v1/public/marts/contracts_daily/csv?dt=2026-08-19",
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "dt=2026-08-19" in response.headers["location"]

    def test_missing_format_on_date_is_404(self, client: TestClient) -> None:
        # supplier_stats has only a CSV export.
        response = client.get("/v1/public/marts/supplier_stats/parquet")
        assert response.status_code == 404

    def test_missing_date_is_404(self, client: TestClient) -> None:
        response = client.get("/v1/public/marts/contracts_daily/csv?dt=2020-01-01")
        assert response.status_code == 404

    def test_excluded_mart_is_404_fail_closed(self, client: TestClient) -> None:
        """A mart outside the LGPD allowlist is never served."""
        response = client.get("/v1/public/marts/data_quality_daily/csv")
        assert response.status_code == 404

    def test_unknown_mart_is_404(self, client: TestClient) -> None:
        response = client.get("/v1/public/marts/no_such_mart/csv")
        assert response.status_code == 404


class TestMethodology:
    def test_methodology_document(self, client: TestClient) -> None:
        response = client.get("/v1/public/methodology")

        assert response.status_code == 200
        body = response.json()
        assert body["export"]["bucket"] == PUBLIC_EXPORT_BUCKET
        assert body["export"]["formats"] == ["csv", "parquet"]
        # LGPD classification is published with the methodology.
        assert "contracts_daily" in body["lgpd_classification"]["exported"]
        assert "pod_usage_hourly" in body["lgpd_classification"]["excluded"]
        # Repo layout available in tests: dbt schema + pipeline specs load.
        mart_names = {m["name"] for m in body["marts"]}
        assert "contracts_daily" in mart_names
        pipeline_names = {p["name"] for p in body["pipelines"]}
        assert "daily_pncp" in pipeline_names
