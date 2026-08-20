"""Tests for the municipal subscription API endpoints (/v1/subscriptions).

Responsibility: Validate the public subscription lifecycle routes and the
O12 hook on the triage ``published`` transition, with the ArangoDB access
layer and the e-mail dispatch mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from capiba.api.main import app
from capiba.api.routers import subscriptions as subscriptions_router
from capiba.db import subscriptions, triage


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


def _fake_aql(db: FakeDb, query: str, bind_vars: dict[str, Any] | None = None) -> list[Any]:
    """Implements the subscription AQL reads over the fake collection."""
    binds = bind_vars or {}
    docs = list(db.col.docs.values())
    if "digest" in binds:
        return [d for d in docs if d.get("token_hash") == binds["digest"]]
    return [
        d["email"]
        for d in docs
        if d.get("status") == binds["status"] and d.get("ibge_code") == binds["ibge"]
    ]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    """Fixture: fake db injected via services.get_db and subscription AQL reads."""
    fake = FakeDb()
    monkeypatch.setattr(subscriptions, "execute_aql", _fake_aql)
    monkeypatch.setattr("capiba.api.services.get_db", lambda: fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client."""
    return TestClient(app)


def _subscribe(client: TestClient, email: str = "ana@example.org") -> str:
    """Subscribes and returns the token captured from the confirmation send."""
    with patch.object(subscriptions_router.subscription_alerts, "send_confirmation") as send:
        send.side_effect = lambda email_, municipality, token: True
        response = client.post(
            "/v1/subscriptions", json={"email": email, "ibge_code": "2611606"}
        )
        assert response.status_code == 201
        return str(send.call_args.args[2])


class TestCreateSubscription:
    """Tests for POST /v1/subscriptions."""

    def test_happy_path_sends_confirmation(self, client: TestClient, db: FakeDb) -> None:
        """A valid subscription is pending and triggers the confirmation e-mail."""
        with patch.object(
            subscriptions_router.subscription_alerts, "send_confirmation", return_value=True
        ) as send:
            response = client.post(
                "/v1/subscriptions",
                json={"email": "Ana@Example.org", "ibge_code": "2611606"},
            )

        assert response.status_code == 201
        assert response.json()["status"] == "pending"
        send.assert_called_once()
        (doc,) = db.col.docs.values()
        assert doc["email"] == "ana@example.org"
        assert doc["status"] == "pending"

    def test_unknown_ibge_is_422(self, client: TestClient, db: FakeDb) -> None:
        """An IBGE code outside the reference is rejected before any write."""
        response = client.post(
            "/v1/subscriptions", json={"email": "ana@example.org", "ibge_code": "0000000"}
        )
        assert response.status_code == 422
        assert db.col.docs == {}

    def test_invalid_payload_is_422(self, client: TestClient, db: FakeDb) -> None:
        """Bad e-mail or non-7-digit codes fail schema validation."""
        assert (
            client.post(
                "/v1/subscriptions", json={"email": "not-an-email", "ibge_code": "2611606"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/subscriptions", json={"email": "ana@example.org", "ibge_code": "123"}
            ).status_code
            == 422
        )

    def test_confirmed_pair_sends_no_email(self, client: TestClient, db: FakeDb) -> None:
        """Re-posting a confirmed pair answers generically without e-mail."""
        token = _subscribe(client)
        assert client.get(f"/v1/subscriptions/confirm?token={token}").status_code == 200

        with patch.object(
            subscriptions_router.subscription_alerts, "send_confirmation"
        ) as send:
            response = client.post(
                "/v1/subscriptions", json={"email": "ana@example.org", "ibge_code": "2611606"}
            )

        assert response.status_code == 201
        send.assert_not_called()


class TestConfirmUnsubscribe:
    """Tests for GET /v1/subscriptions/confirm and /unsubscribe."""

    def test_confirm_then_unsubscribe(self, client: TestClient, db: FakeDb) -> None:
        """The token from the confirmation e-mail drives both transitions."""
        token = _subscribe(client)

        confirmed = client.get(f"/v1/subscriptions/confirm?token={token}")
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "confirmed"

        cancelled = client.get(f"/v1/subscriptions/unsubscribe?token={token}")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "unsubscribed"

    def test_invalid_token_is_404(self, client: TestClient, db: FakeDb) -> None:
        """An unmatched token is a 404 on both token routes."""
        assert client.get("/v1/subscriptions/confirm?token=x" + "0" * 30).status_code == 404
        assert client.get("/v1/subscriptions/unsubscribe?token=x" + "0" * 30).status_code == 404


class TestUnavailableDatabase:
    """A persistence failure surfaces as 503 on every route (never 500)."""

    def test_subscribe_failure_is_503(self, client: TestClient, db: FakeDb) -> None:
        with patch.object(
            subscriptions, "subscribe", side_effect=RuntimeError("arango down")
        ):
            response = client.post(
                "/v1/subscriptions",
                json={"email": "ana@example.org", "ibge_code": "2611606"},
            )
        assert response.status_code == 503
        assert response.json()["detail"] == "ArangoDB database unavailable"

    def test_confirm_failure_is_503(self, client: TestClient, db: FakeDb) -> None:
        with patch.object(
            subscriptions, "confirm", side_effect=RuntimeError("arango down")
        ):
            response = client.get("/v1/subscriptions/confirm?token=x" + "0" * 30)
        assert response.status_code == 503

    def test_unsubscribe_failure_is_503(self, client: TestClient, db: FakeDb) -> None:
        with patch.object(
            subscriptions, "unsubscribe", side_effect=RuntimeError("arango down")
        ):
            response = client.get("/v1/subscriptions/unsubscribe?token=x" + "0" * 30)
        assert response.status_code == 503


class TestPublishedHook:
    """Tests for the O12 hook on POST /v1/triage/signals/{key}/review."""

    SIGNAL = {
        "entity_type": "supplier",
        "entity_id": "12345678000199",
        "signal_type": "single_bid",
        "score": 0.8,
        "details": "{}",
    }
    KEY = "supplier:12345678000199:single_bid"

    @pytest.fixture
    def triage_db(self, monkeypatch: pytest.MonkeyPatch, db: FakeDb) -> FakeDb:
        """Fixture: the same fake db wired to the triage AQL reads."""
        monkeypatch.setattr(
            triage,
            "execute_aql",
            lambda db_, query, bind_vars=None: list(db_.col.docs.values()),
        )
        return db

    def test_publish_triggers_broadcast(self, client: TestClient, triage_db: FakeDb) -> None:
        """Transitioning to published notifies the subscribers."""
        triage.register_signals(triage_db, [self.SIGNAL])
        triage.apply_review(triage_db, self.KEY, triage.TriageStatus.CONFIRMED, "ana")

        with patch(
            "capiba.api.routers.triage.subscription_alerts.notify_published_signal"
        ) as notify:
            response = client.post(
                f"/v1/triage/signals/{self.KEY}/review",
                json={"status": "published", "reviewer": "ana"},
            )

        assert response.status_code == 200
        notify.assert_called_once()
        assert notify.call_args.args[1]["status"] == "published"

    def test_confirm_does_not_trigger_broadcast(
        self, client: TestClient, triage_db: FakeDb
    ) -> None:
        """Confirmed/rejected transitions never notify subscribers."""
        triage.register_signals(triage_db, [self.SIGNAL])

        with patch(
            "capiba.api.routers.triage.subscription_alerts.notify_published_signal"
        ) as notify:
            response = client.post(
                f"/v1/triage/signals/{self.KEY}/review",
                json={"status": "confirmed", "reviewer": "ana"},
            )

        assert response.status_code == 200
        notify.assert_not_called()
