"""Tests for the municipal alert subscriptions persistence (capiba.db.subscriptions).

Responsibility: Validate the subscription lifecycle (pending → confirmed →
unsubscribed), the token model (opaque, hash-only at rest) and the
enumeration-safe idempotency, with ArangoDB mocked.
"""

from __future__ import annotations

from typing import Any

import pytest

from capiba.db import subscriptions
from capiba.db.subscriptions import SubscriptionStatus


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
    """Implements the two subscription AQL reads over the fake collection."""
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
    """Fixture: fake db with AQL reads routed to the fake collection."""
    fake = FakeDb()
    monkeypatch.setattr(subscriptions, "execute_aql", _fake_aql)
    return fake


class TestSubscribe:
    """Tests for subscribe (create / re-arm / enumeration-safe)."""

    def test_creates_pending_with_hashed_token(self, db: FakeDb) -> None:
        """A new subscription is pending and persists only the token digest."""
        doc, token = subscriptions.subscribe(db, "Ana@Example.org", "2611606")

        assert token is not None
        assert doc["status"] == "pending"
        assert doc["email"] == "ana@example.org"
        assert doc["ibge_code"] == "2611606"
        stored = db.col.docs[doc["_key"]]
        assert stored["token_hash"] != token
        assert token not in str(stored)

    def test_resubscribe_while_pending_rotates_token(self, db: FakeDb) -> None:
        """Re-posting a pending subscription rotates the token (resend)."""
        doc1, token1 = subscriptions.subscribe(db, "ana@example.org", "2611606")
        doc2, token2 = subscriptions.subscribe(db, "ana@example.org", "2611606")

        assert len(db.col.docs) == 1
        assert doc2["_key"] == doc1["_key"]
        assert token2 is not None and token2 != token1
        assert subscriptions.confirm(db, token1) is None
        assert subscriptions.confirm(db, token2) is not None

    def test_confirmed_pair_returns_no_token(self, db: FakeDb) -> None:
        """Re-posting a confirmed subscription sends no e-mail (no enumeration)."""
        _, token = subscriptions.subscribe(db, "ana@example.org", "2611606")
        assert token is not None
        subscriptions.confirm(db, token)

        doc, new_token = subscriptions.subscribe(db, "ana@example.org", "2611606")

        assert new_token is None
        assert doc["status"] == "confirmed"

    def test_resubscribe_after_unsubscribe_requires_confirmation(self, db: FakeDb) -> None:
        """An unsubscribed pair goes back to pending with a fresh token."""
        _, token = subscriptions.subscribe(db, "ana@example.org", "2611606")
        assert token is not None
        subscriptions.confirm(db, token)
        subscriptions.unsubscribe(db, token)

        doc, new_token = subscriptions.subscribe(db, "ana@example.org", "2611606")

        assert new_token is not None
        assert doc["status"] == "pending"
        assert subscriptions.confirmed_emails_by_ibge(db, "2611606") == []


class TestConfirmUnsubscribe:
    """Tests for the token-driven transitions."""

    def test_confirm_happy_path(self, db: FakeDb) -> None:
        """The raw token confirms the pending subscription."""
        _, token = subscriptions.subscribe(db, "ana@example.org", "2611606")
        assert token is not None

        doc = subscriptions.confirm(db, token)

        assert doc is not None
        assert doc["status"] == str(SubscriptionStatus.CONFIRMED)
        assert doc["confirmed_at"] is not None

    def test_confirm_is_idempotent(self, db: FakeDb) -> None:
        """Confirming twice keeps the confirmed state."""
        _, token = subscriptions.subscribe(db, "ana@example.org", "2611606")
        assert token is not None
        subscriptions.confirm(db, token)
        doc = subscriptions.confirm(db, token)
        assert doc is not None and doc["status"] == "confirmed"

    def test_unknown_token_returns_none(self, db: FakeDb) -> None:
        """An unmatched token confirms/unsubscribes nothing."""
        assert subscriptions.confirm(db, "bogus-token") is None
        assert subscriptions.unsubscribe(db, "bogus-token") is None

    def test_unsubscribe_happy_path(self, db: FakeDb) -> None:
        """The same management token unsubscribes (idempotent)."""
        _, token = subscriptions.subscribe(db, "ana@example.org", "2611606")
        assert token is not None
        subscriptions.confirm(db, token)

        doc = subscriptions.unsubscribe(db, token)

        assert doc is not None
        assert doc["status"] == str(SubscriptionStatus.UNSUBSCRIBED)
        assert doc["unsubscribed_at"] is not None
        again = subscriptions.unsubscribe(db, token)
        assert again is not None and again["status"] == "unsubscribed"


class TestConfirmedEmailsByIbge:
    """Tests for the broadcast recipient lookup."""

    def test_filters_by_status_and_municipality(self, db: FakeDb) -> None:
        """Only confirmed subscribers of the municipality are returned."""
        _, token_a = subscriptions.subscribe(db, "ana@example.org", "2611606")
        _, token_b = subscriptions.subscribe(db, "bruno@example.org", "2611606")
        subscriptions.subscribe(db, "carla@example.org", "3550308")
        assert token_a is not None and token_b is not None
        subscriptions.confirm(db, token_a)

        assert subscriptions.confirmed_emails_by_ibge(db, "2611606") == ["ana@example.org"]
        assert subscriptions.confirmed_emails_by_ibge(db, "3550308") == []

        subscriptions.confirm(db, token_b)
        assert subscriptions.confirmed_emails_by_ibge(db, "2611606") == [
            "ana@example.org",
            "bruno@example.org",
        ]


class FakeDbWithoutCollection(FakeDb):
    """Fake db where the subscriptions collection does not exist yet."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[str] = []

    def has_collection(self, name: str) -> bool:
        return False

    def create_collection(self, name: str) -> None:
        self.created.append(name)


class TestMissingCollection:
    """The persistence layer degrades gracefully before the first write."""

    def test_ensure_creates_the_collection_lazily(self) -> None:
        db = FakeDbWithoutCollection()
        col = subscriptions.ensure_subscriptions_collection(db)
        assert db.created == [subscriptions.SUBSCRIPTIONS_COLLECTION]
        assert col is db.col

    def test_token_reads_return_none_without_collection(self) -> None:
        db = FakeDbWithoutCollection()
        assert subscriptions.confirm(db, "any-token") is None
        assert subscriptions.unsubscribe(db, "any-token") is None

    def test_confirmed_emails_empty_without_collection(self) -> None:
        db = FakeDbWithoutCollection()
        assert subscriptions.confirmed_emails_by_ibge(db, "2611606") == []
