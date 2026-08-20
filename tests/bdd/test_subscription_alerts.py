"""BDD step definitions for the municipal alert subscriptions (O12).

Feature file: tests/bdd/features/subscription_alerts.feature
Offline: ArangoDB is faked and the e-mail dispatch is captured.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.db import subscriptions
from capiba.notification import subscriptions as alerts

scenarios("features/subscription_alerts.feature")


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
def context() -> dict[str, Any]:
    """Shared scenario state (fake db, tokens, captured e-mails)."""
    return {"db": FakeDb(), "tokens": {}, "sent": []}


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch, context: dict[str, Any]) -> None:
    """Routes AQL reads to the fake db and captures the e-mail dispatch."""
    monkeypatch.setattr(subscriptions, "execute_aql", _fake_aql)
    monkeypatch.setattr(alerts, "execute_aql", lambda db, query, bind_vars=None: [])
    monkeypatch.setattr(
        alerts,
        "_dispatch",
        lambda alert: context["sent"].append(alert) or True,
    )


def _subscribe(context: dict[str, Any], email: str, ibge: str, confirm: bool) -> None:
    """Subscribes and (optionally) confirms, tracking the token."""
    _, token = subscriptions.subscribe(context["db"], email, ibge)
    if token is not None:
        context["tokens"][email] = token
    if confirm and token is not None:
        subscriptions.confirm(context["db"], token)


@given(parsers.parse('"{email}" has a confirmed subscription to "{ibge}"'))
def given_confirmed(context: dict[str, Any], email: str, ibge: str) -> None:
    """A confirmed subscription exists."""
    _subscribe(context, email, ibge, confirm=True)


@given(parsers.parse('"{email}" has a pending subscription to "{ibge}"'))
def given_pending(context: dict[str, Any], email: str, ibge: str) -> None:
    """A pending (unconfirmed) subscription exists."""
    _subscribe(context, email, ibge, confirm=False)


@when(parsers.parse('"{email}" subscribes to municipality "{ibge}"'))
def when_subscribe(context: dict[str, Any], email: str, ibge: str) -> None:
    """A journalist subscribes by the IBGE code."""
    _subscribe(context, email, ibge, confirm=False)


@when("the management token is used to confirm")
def when_confirm(context: dict[str, Any]) -> None:
    """The token delivered by e-mail confirms the subscription."""
    subscriptions.confirm(context["db"], context["tokens"]["ana@example.org"])


@when("the management token is used to unsubscribe")
def when_unsubscribe(context: dict[str, Any]) -> None:
    """The same token unsubscribes."""
    subscriptions.unsubscribe(context["db"], context["tokens"]["ana@example.org"])


def _publish(context: dict[str, Any], signal_type: str, entity_id: str, details: dict) -> None:
    """Broadcasts a published signal (the O12 trigger, best-effort)."""
    signal = {
        "entity_type": "supplier",
        "entity_id": entity_id,
        "signal_type": signal_type,
        "score": 0.9,
        "details": json.dumps(details),
    }
    alerts.notify_published_signal(context["db"], signal)


@when(
    parsers.parse(
        'a "{signal_type}" signal of supplier "{entity_id}" for "{city}"/"{uf}" is published'
    )
)
def when_publish_geo(context: dict[str, Any], signal_type: str, entity_id: str, city: str, uf: str) -> None:
    """A triage-published signal whose details carry the buyer municipality."""
    _publish(context, signal_type, entity_id, {"city": city, "uf": uf})


@when(parsers.parse('a "{signal_type}" signal of supplier "{entity_id}" without municipality is published'))
def when_publish_no_geo(context: dict[str, Any], signal_type: str, entity_id: str) -> None:
    """A published signal whose municipality cannot be resolved."""
    _publish(context, signal_type, entity_id, {})


@then(parsers.parse('the subscription of "{email}" to "{ibge}" is "{status}"'))
def then_status(context: dict[str, Any], email: str, ibge: str, status: str) -> None:
    """The subscription is in the expected lifecycle state."""
    doc = context["db"].col.get(subscriptions.subscription_key(email, ibge))
    assert doc is not None
    assert doc["status"] == status


@then(parsers.parse("{count:d} alert e-mail is sent"))
def then_sent_count(context: dict[str, Any], count: int) -> None:
    """Exactly this many subscriber e-mails were dispatched."""
    assert len(context["sent"]) == count


@then("no alert e-mail is sent")
def then_nothing_sent(context: dict[str, Any]) -> None:
    """No subscriber e-mail was dispatched."""
    assert context["sent"] == []


@then("the alert e-mail links the evidence package of the signal")
def then_evidence_link(context: dict[str, Any]) -> None:
    """The alert carries the O9 evidence package URL of the signal key."""
    (alert,) = context["sent"]
    url = alert.metadata["evidence_url"]
    assert "/v1/signals/" in url
    assert url.endswith("/evidence")
