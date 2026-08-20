"""Tests for the graph API endpoints.

Responsibility: Validate the ownership-tracing endpoint with ArangoDB
mocked via dependency override (no live database).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from capiba.api.main import app
from capiba.api.routers import graph
from capiba.api.routers.graph import get_db

CNPJ = "12345678000195"


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client."""
    return TestClient(app)


@pytest.fixture
def db() -> Any:
    """Fixture: ArangoDB mock injected via dependency override."""
    mock = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_db, None)


class TestOwnership:
    """Tests for GET /v1/graph/ownership/{cnpj}."""

    def test_ownership_paths(self, client: TestClient, db: MagicMock) -> None:
        """Paths returned by trace_ownership must be served as-is."""
        paths = [[CNPJ, "PARTNER1"], [CNPJ, "PARTNER2", "SHELL1"]]
        db.aql.execute.return_value = iter(paths)

        response = client.get(f"/v1/graph/ownership/{CNPJ}")

        assert response.status_code == 200
        body = response.json()
        assert body["entity"] == CNPJ
        assert body["max_depth"] == 3
        assert body["paths"] == paths

    def test_ownership_max_depth_query_param(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """The max_depth query param must reach the AQL bind variables."""
        db.aql.execute.return_value = iter([])

        response = client.get(f"/v1/graph/ownership/{CNPJ}?max_depth=5")

        assert response.status_code == 200
        assert response.json()["max_depth"] == 5
        bind_vars = db.aql.execute.call_args.kwargs["bind_vars"]
        assert bind_vars["maxDepth"] == 5
        assert bind_vars["cnpj"] == "12345678"  # cnpj_basico (vertex key)

    def test_ownership_unknown_cnpj_returns_empty(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """A CNPJ without ownership edges must return an empty path list."""
        db.aql.execute.return_value = iter([])

        response = client.get(f"/v1/graph/ownership/{CNPJ}")

        assert response.status_code == 200
        assert response.json()["paths"] == []

    def test_ownership_invalid_cnpj_returns_422(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """A malformed CNPJ must return a validation error."""
        response = client.get("/v1/graph/ownership/123")

        assert response.status_code == 422
        db.aql.execute.assert_not_called()

    def test_ownership_invalid_max_depth_returns_422(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """A max_depth outside the 1-10 range must return a validation error."""
        response = client.get(f"/v1/graph/ownership/{CNPJ}?max_depth=0")

        assert response.status_code == 422
        db.aql.execute.assert_not_called()

    def test_ownership_db_failure_returns_503(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """A database failure during the traversal must return 503."""
        db.aql.execute.side_effect = ConnectionError("arango down")

        response = client.get(f"/v1/graph/ownership/{CNPJ}")

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]

    def test_get_db_wraps_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_db must convert ArangoDB connection failures into 503."""

        def _broken() -> Any:
            raise ConnectionError("arango down")

        monkeypatch.setattr(graph, "get_capiba_db", _broken)

        with pytest.raises(HTTPException) as exc_info:
            get_db()
        assert exc_info.value.status_code == 503


class TestPartnersOfBuyer:
    """Tests for GET /v1/graph/partners/{siafi_code}."""

    def test_partners_served(self, client: TestClient, db: MagicMock) -> None:
        """Rows returned by partners_of_buyer must be served as-is."""
        rows = [
            {
                "supplier_cnpj": CNPJ,
                "company": "12345678",
                "edge": "ownership",
                "partner_key": "p1",
                "partner_schema": "Person",
                "partner_name": "JOAO SILVA",
            }
        ]
        db.aql.execute.return_value = iter(rows)

        response = client.get("/v1/graph/partners/900000")

        assert response.status_code == 200
        body = response.json()
        assert body["siafi_code"] == "900000"
        assert body["partners"] == rows
        bind_vars = db.aql.execute.call_args.kwargs["bind_vars"]
        assert bind_vars["siafiCode"] == "900000"

    def test_invalid_siafi_code_returns_422(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """A non-numeric SIAFI code must return a validation error."""
        response = client.get("/v1/graph/partners/ABC")

        assert response.status_code == 422
        db.aql.execute.assert_not_called()

    def test_db_failure_returns_503(self, client: TestClient, db: MagicMock) -> None:
        """A database failure during the traversal must return 503."""
        db.aql.execute.side_effect = ConnectionError("arango down")

        response = client.get("/v1/graph/partners/900000")

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]


class TestFtmExport:
    """Tests for GET /v1/graph/ftm/{cnpj}."""

    def test_ftm_entities_served(self, client: TestClient, db: MagicMock) -> None:
        """The subgraph exports as FtM entities over HTTP."""
        db.aql.execute.return_value = iter(
            [
                {
                    "company": {
                        "_id": "companies/12345678",
                        "_key": "12345678",
                        "razao_social": "ACME LTDA",
                        "cnpj_basico": "12345678",
                    },
                    "inbound": [],
                    "outbound": [],
                }
            ]
        )

        response = client.get(f"/v1/graph/ftm/{CNPJ}")

        assert response.status_code == 200
        body = response.json()
        assert body["entity"] == CNPJ
        assert body["entities"] == [
            {
                "id": "company-12345678",
                "schema": "Company",
                "properties": {
                    "name": ["ACME LTDA"],
                    "registrationNumber": ["12345678"],
                },
            }
        ]

    def test_unknown_cnpj_returns_empty_entities(
        self, client: TestClient, db: MagicMock
    ) -> None:
        """A CNPJ absent from the graph exports an empty entity list."""
        db.aql.execute.return_value = iter(
            [{"company": None, "inbound": [], "outbound": []}]
        )

        response = client.get(f"/v1/graph/ftm/{CNPJ}")

        assert response.status_code == 200
        assert response.json()["entities"] == []

    def test_invalid_cnpj_returns_422(self, client: TestClient, db: MagicMock) -> None:
        """A malformed CNPJ must return a validation error."""
        response = client.get("/v1/graph/ftm/123")

        assert response.status_code == 422
        db.aql.execute.assert_not_called()

    def test_db_failure_returns_503(self, client: TestClient, db: MagicMock) -> None:
        """A database failure during the export must return 503."""
        db.aql.execute.side_effect = ConnectionError("arango down")

        response = client.get(f"/v1/graph/ftm/{CNPJ}")

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]
