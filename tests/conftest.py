"""Shared fixtures for tests.

Responsibility: Provide reusable test data
for all test modules.
"""

from __future__ import annotations

import os

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
