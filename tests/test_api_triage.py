"""Tests for the editorial triage API endpoints (/v1/triage).

Responsibility: Validate the triage routes with the ArangoDB
access layer mocked (no live database).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from capiba.api.main import app
from capiba.db import triage


class FakeCollection:
    """Dict-backed stand-in for an ArangoDB collection."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.docs.get(key)

    def insert(self, doc: dict[str, Any], silent: bool = False) -> dict[str, Any]:
        self.docs[doc["_key"]] = dict(doc)
        return dict(doc)

    def update(self, doc: dict[str, Any]) -> dict[str, Any]:
        self.docs[doc["_key"]].update(doc)
        return dict(self.docs[doc["_key"]])


class FakeDb:
    """Minimal ArangoDB stand-in wired to a single fake collection."""

    def __init__(self) -> None:
        self.col = FakeCollection()

    def has_collection(self, name: str) -> bool:
        return True

    def create_collection(self, name: str) -> None:
        pass

    def collection(self, name: str) -> FakeCollection:
        return self.col


SIGNAL = {
    "entity_type": "supplier",
    "entity_id": "12345678000199",
    "signal_type": "single_bid",
    "score": 0.8,
    "details": "{}",
}
KEY = "supplier:12345678000199:single_bid"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    """Fixture: fake db injected via services.get_db and triage AQL reads."""
    fake = FakeDb()
    monkeypatch.setattr(
        triage,
        "execute_aql",
        lambda db_, query, bind_vars=None: list(db_.col.docs.values()),
    )
    monkeypatch.setattr("capiba.api.services.get_db", lambda: fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client."""
    return TestClient(app)


class TestListTriageSignals:
    """Tests for GET /v1/triage/signals."""

    def test_lists_pending_signals(self, client: TestClient, db: FakeDb) -> None:
        """Registered signals are listed with their editorial state."""
        triage.register_signals(db, [SIGNAL])

        response = client.get("/v1/triage/signals")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["key"] == KEY
        assert data[0]["status"] == "pending_review"
        assert data[0]["score"] == 0.8

    def test_filters_by_status(self, client: TestClient, db: FakeDb) -> None:
        """The status filter narrows the listing."""
        triage.register_signals(db, [SIGNAL])
        triage.apply_review(db, KEY, triage.TriageStatus.CONFIRMED, "ana")

        assert client.get("/v1/triage/signals?status=pending_review").json() == []
        confirmed = client.get("/v1/triage/signals?status=confirmed").json()
        assert [s["key"] for s in confirmed] == [KEY]

    def test_invalid_status_is_422(self, client: TestClient, db: FakeDb) -> None:
        """An unknown status value fails schema validation."""
        assert client.get("/v1/triage/signals?status=bogus").status_code == 422

    def test_db_unavailable_is_503(self, client: TestClient) -> None:
        """Database failures map to 503."""
        with patch(
            "capiba.api.services.get_db",
            side_effect=HTTPException(status_code=503, detail="ArangoDB database unavailable"),
        ):
            assert client.get("/v1/triage/signals").status_code == 503


class TestReviewSignal:
    """Tests for POST /v1/triage/signals/{key}/review."""

    def test_confirm_signal(self, client: TestClient, db: FakeDb) -> None:
        """A reviewer confirms a pending signal."""
        triage.register_signals(db, [SIGNAL])

        response = client.post(
            f"/v1/triage/signals/{KEY}/review",
            json={"status": "confirmed", "reviewer": "ana"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "confirmed"
        assert data["reviewed_by"] == "ana"

    def test_reject_requires_reason(self, client: TestClient, db: FakeDb) -> None:
        """Rejection without a reason is a 422."""
        triage.register_signals(db, [SIGNAL])

        response = client.post(
            f"/v1/triage/signals/{KEY}/review",
            json={"status": "rejected", "reviewer": "ana"},
        )

        assert response.status_code == 422
        assert db.col.docs[KEY]["status"] == "pending_review"

    def test_unknown_signal_is_404(self, client: TestClient, db: FakeDb) -> None:
        """Reviewing an unregistered signal is a 404."""
        response = client.post(
            "/v1/triage/signals/supplier:0:single_bid/review",
            json={"status": "confirmed", "reviewer": "ana"},
        )
        assert response.status_code == 404

    def test_published_is_terminal(self, client: TestClient, db: FakeDb) -> None:
        """Transitions out of published are a 422."""
        triage.register_signals(db, [SIGNAL])
        triage.apply_review(db, KEY, triage.TriageStatus.CONFIRMED, "ana")
        triage.apply_review(db, KEY, triage.TriageStatus.PUBLISHED, "ana")

        response = client.post(
            f"/v1/triage/signals/{KEY}/review",
            json={"status": "rejected", "reviewer": "bruno", "reason": "tarde"},
        )

        assert response.status_code == 422

    def test_empty_reviewer_is_422(self, client: TestClient, db: FakeDb) -> None:
        """Schema validation rejects an empty reviewer."""
        triage.register_signals(db, [SIGNAL])
        response = client.post(
            f"/v1/triage/signals/{KEY}/review",
            json={"status": "confirmed", "reviewer": ""},
        )
        assert response.status_code == 422


class TestTriageMetrics:
    """Tests for GET /v1/triage/metrics."""

    def test_precision_report(self, client: TestClient, db: FakeDb) -> None:
        """The report aggregates human labels per operator."""
        triage.register_signals(
            db,
            [SIGNAL, {**SIGNAL, "entity_id": "98765432000196"}],
        )
        triage.apply_review(db, KEY, triage.TriageStatus.CONFIRMED, "ana")
        triage.apply_review(
            db,
            "supplier:98765432000196:single_bid",
            triage.TriageStatus.REJECTED,
            "ana",
            reason="falso positivo",
        )

        response = client.get("/v1/triage/metrics")

        assert response.status_code == 200
        (row,) = response.json()
        assert row["signal_type"] == "single_bid"
        assert row["confirmed"] == 1
        assert row["rejected"] == 1
        assert row["precision"] == 0.5

    def test_empty_report(self, client: TestClient, db: FakeDb) -> None:
        """Without entries the report is an empty list."""
        assert client.get("/v1/triage/metrics").json() == []


class TestDatabaseFailures:
    """A persistence-layer exception surfaces as 503 (never 500)."""

    def test_list_failure_is_503(self, client: TestClient, db: FakeDb) -> None:
        with patch.object(
            triage, "list_reviews", side_effect=RuntimeError("arango down")
        ):
            response = client.get("/v1/triage/signals")
        assert response.status_code == 503
        assert response.json()["detail"] == "ArangoDB database unavailable"

    def test_review_failure_is_503(self, client: TestClient, db: FakeDb) -> None:
        with patch.object(
            triage, "apply_review", side_effect=RuntimeError("arango down")
        ):
            response = client.post(
                f"/v1/triage/signals/{KEY}/review",
                json={"status": "confirmed", "reviewer": "ana"},
            )
        assert response.status_code == 503

    def test_metrics_failure_is_503(self, client: TestClient, db: FakeDb) -> None:
        with patch.object(
            triage, "precision_report", side_effect=RuntimeError("arango down")
        ):
            response = client.get("/v1/triage/metrics")
        assert response.status_code == 503
