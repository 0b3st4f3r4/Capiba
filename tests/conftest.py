"""Shared fixtures for tests.

Responsibility: Provide reusable test data
for all test modules.
"""

from __future__ import annotations

import os
from typing import Any, NoReturn

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Registers the integration and slow test markers."""
    config.addinivalue_line(
        "markers",
        "integration: requires live infra (ArangoDB etc.); run with CAPIBA_INTEGRATION=1",
    )
    config.addinivalue_line(
        "markers",
        "slow: regime/calibration tests (detection batteries); run with CAPIBA_SLOW=1",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skips integration/slow tests unless the corresponding env var is set."""
    skip_integration = pytest.mark.skip(
        reason="requires live infra; set CAPIBA_INTEGRATION=1"
    )
    skip_slow = pytest.mark.skip(reason="regime test; set CAPIBA_SLOW=1")
    run_integration = bool(os.getenv("CAPIBA_INTEGRATION"))
    run_slow = bool(os.getenv("CAPIBA_SLOW"))
    for item in items:
        if not run_integration and "integration" in item.keywords:
            item.add_marker(skip_integration)
        if not run_slow and "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(autouse=True)
def _block_real_infra(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocks real infra clients in non-integration tests.

    Unit tests must mock infra clients (pattern: ``tests/test_pipeline.py``).
    Without mocks, tests with active port-forwards leak to the real ArangoDB /
    MinIO / Trino / Redis and blow the 2-minute budget of the fast suite.
    This fixture makes the lowest-level connection/construction points fail
    fast with a message pointing at the mock to add. The offline degradation
    paths (SQLite Iceberg catalog, ``RedisError`` handling) keep working.
    """
    if os.getenv("CAPIBA_INTEGRATION") or request.node.get_closest_marker(
        "integration"
    ):
        return

    import arango.client
    import minio
    import redis

    from capiba.config import TRINO_URL
    from capiba.pipeline import trino

    def _blocked_arango_init(self: object, *args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError(
            "test tried to build a real ArangoDB client; mock "
            "`get_capiba_db` at the importing module (pattern: "
            "tests/test_pipeline.py) or mark the test "
            "@pytest.mark.integration"
        )

    def _blocked_minio_init(self: object, *args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError(
            "test tried to build a real MinIO client; mock "
            "`capiba.pipeline.lake.get_client` / the MinIO storage "
            "dependency, or mark the test @pytest.mark.integration"
        )

    def _blocked_redis_from_url(*args: Any, **kwargs: Any) -> NoReturn:
        # RedisError subclass: graceful-degradation paths keep behaving as
        # they do offline (connection refused), but a live Redis no longer
        # answers silently.
        raise redis.ConnectionError(
            "test tried to connect to a real Redis; mock `redis.from_url` "
            "or mark the test @pytest.mark.integration"
        )

    def _trino_guard(real: Any) -> Any:
        def _guarded(url: str, *args: Any, **kwargs: Any) -> Any:
            if str(url).startswith(TRINO_URL):
                raise RuntimeError(
                    "test tried to reach the real Trino gateway; mock "
                    "`capiba.pipeline.trino.run_query` (pattern: "
                    "tests/test_lake.py) or mark the test "
                    "@pytest.mark.integration"
                )
            return real(url, *args, **kwargs)

        return _guarded

    monkeypatch.setattr(arango.client.ArangoClient, "__init__", _blocked_arango_init)
    monkeypatch.setattr(minio.Minio, "__init__", _blocked_minio_init)
    monkeypatch.setattr(redis, "from_url", _blocked_redis_from_url)
    # trino.run_query itself is left alone — test_trino.py exercises it with
    # a mocked transport; the guard wraps only calls bound for TRINO_URL.
    monkeypatch.setattr(trino.requests, "post", _trino_guard(trino.requests.post))
    monkeypatch.setattr(trino.requests, "get", _trino_guard(trino.requests.get))


@pytest.fixture
def sample_contracts() -> list[dict[str, object]]:
    """Fixture: sample contracts for tests."""
    return [
        {
            "id": "C001",
            "process_number": "P001/2026",
            "subject": "Aquisição de material de escritório",
            "amount": 15000.00,
            "signature_date": "2026-01-15",
            "validity_start": "2026-01-15",
            "validity_end": "2026-12-31",
            "buyer": {
                "siafi_code": "123456",
                "name": "Prefeitura Municipal de Exemplo",
                "government_level": "municipal",
                "uf": "MG",
                "city": "Belo Horizonte",
            },
            "supplier": {
                "cnpj": "12345678000195",
                "legal_name": "Fornecedora Exemplo Ltda",
                "primary_cnae": "4761000",
                "state": "MG",
                "city": "Belo Horizonte",
            },
            "modality": "pregao",
            "status": "concluido",
        },
        {
            "id": "C002",
            "process_number": "P002/2026",
            "subject": "Serviços de limpeza",
            "amount": 50000.00,
            "signature_date": "2026-02-01",
            "validity_start": "2026-02-01",
            "validity_end": "2026-12-31",
            "buyer": {
                "siafi_code": "123456",
                "name": "Prefeitura Municipal de Exemplo",
                "government_level": "municipal",
                "uf": "MG",
                "city": "Belo Horizonte",
            },
            "supplier": {
                "cnpj": "98765432000196",
                "legal_name": "Limpeza Total Ltda",
                "primary_cnae": "8121400",
                "state": "SP",
                "city": "São Paulo",
            },
            "modality": "dispensa",
            "status": "em_andamento",
        },
    ]
