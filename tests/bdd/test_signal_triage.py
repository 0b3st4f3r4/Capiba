"""BDD step definitions for the editorial triage of fraud signals.

Feature file: tests/bdd/features/signal_triage.feature
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.db import triage

scenarios("features/signal_triage.feature")


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


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (fake db, signals in, errors out)."""
    return {"db": FakeDb(), "signals": []}


@pytest.fixture(autouse=True)
def _fake_aql(monkeypatch: pytest.MonkeyPatch, context: dict[str, Any]) -> None:
    """Routes the triage AQL reads to the fake collection."""
    monkeypatch.setattr(
        triage,
        "execute_aql",
        lambda db, query, bind_vars=None: list(db.col.docs.values()),
    )


def _signal(signal_type: str, entity_id: str, score: float) -> dict[str, Any]:
    """Builds a detect-shaped signal row for the scenarios."""
    entity_type = "buyer" if signal_type == "concentration" else "supplier"
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "signal_type": signal_type,
        "score": score,
        "details": "{}",
    }


def _key_of(signal: dict[str, Any]) -> str:
    return triage.signal_key(
        signal["entity_type"], signal["entity_id"], signal["signal_type"]
    )


@given(
    parsers.parse(
        'a computed signal "{signal_type}" for {entity_type} "{entity_id}"'
        " with score {score:f}"
    )
)
def computed_signal(
    context: dict[str, Any], signal_type: str, entity_type: str, entity_id: str, score: float
) -> None:
    context["signals"].append(_signal(signal_type, entity_id, score))


@given(parsers.parse('the signal was confirmed by reviewer "{reviewer}"'))
def confirmed_first(context: dict[str, Any], reviewer: str) -> None:
    signal = context["signals"][0]
    triage.register_signals(context["db"], [signal])
    triage.apply_review(
        context["db"], _key_of(signal), triage.TriageStatus.CONFIRMED, reviewer
    )


@given(
    parsers.parse(
        'the signal "{signal_type}" on "{entity_id}" was confirmed'
        ' by reviewer "{reviewer}"'
    )
)
def confirmed_named(
    context: dict[str, Any], signal_type: str, entity_id: str, reviewer: str
) -> None:
    signal = _signal(signal_type, entity_id, 0.0)
    triage.register_signals(context["db"], [signal])
    triage.apply_review(
        context["db"], _key_of(signal), triage.TriageStatus.CONFIRMED, reviewer
    )


@given(parsers.parse('the signal was published by reviewer "{reviewer}"'))
def published_first(context: dict[str, Any], reviewer: str) -> None:
    triage.apply_review(
        context["db"],
        _key_of(context["signals"][0]),
        triage.TriageStatus.PUBLISHED,
        reviewer,
    )


@given(
    parsers.parse(
        'the signal "{signal_type}" on "{entity_id}" was rejected'
        ' by reviewer "{reviewer}" with reason "{reason}"'
    )
)
def rejected_named(
    context: dict[str, Any], signal_type: str, entity_id: str, reviewer: str, reason: str
) -> None:
    signal = _signal(signal_type, entity_id, 0.0)
    triage.register_signals(context["db"], [signal])
    triage.apply_review(
        context["db"],
        _key_of(signal),
        triage.TriageStatus.REJECTED,
        reviewer,
        reason=reason,
    )


@when("the signals are registered for triage")
def register(context: dict[str, Any]) -> None:
    triage.register_signals(context["db"], context["signals"])


@when(
    parsers.parse(
        'a computed signal "{signal_type}" for supplier "{entity_id}"'
        " with score {score:f} is registered again"
    )
)
def register_again(
    context: dict[str, Any], signal_type: str, entity_id: str, score: float
) -> None:
    triage.register_signals(context["db"], [_signal(signal_type, entity_id, score)])


@when(parsers.parse('reviewer "{reviewer}" rejects the signal without a reason'))
def reject_without_reason(context: dict[str, Any], reviewer: str) -> None:
    signal = context["signals"][0]
    triage.register_signals(context["db"], [signal])
    try:
        triage.apply_review(
            context["db"], _key_of(signal), triage.TriageStatus.REJECTED, reviewer
        )
    except triage.TriageError as exc:
        context["error"] = exc


@when(
    parsers.parse(
        'reviewer "{reviewer}" tries to reject the signal with reason "{reason}"'
    )
)
def reject_after_publish(context: dict[str, Any], reviewer: str, reason: str) -> None:
    try:
        triage.apply_review(
            context["db"],
            _key_of(context["signals"][0]),
            triage.TriageStatus.REJECTED,
            reviewer,
            reason=reason,
        )
    except triage.TriageError as exc:
        context["error"] = exc


@when("the precision report is computed")
def precision(context: dict[str, Any]) -> None:
    context["report"] = triage.precision_report(context["db"])


@then(
    parsers.parse(
        'the triage entry for "{signal_type}" on "{entity_id}" has status "{status}"'
    )
)
def entry_status(
    context: dict[str, Any], signal_type: str, entity_id: str, status: str
) -> None:
    matches = [
        doc
        for doc in context["db"].col.docs.values()
        if doc["signal_type"] == signal_type and doc["entity_id"] == entity_id
    ]
    assert matches, f"triage entry for {signal_type} on {entity_id} not found"
    assert matches[0]["status"] == status
    context["entry"] = matches[0]


@then(parsers.parse("the triage entry score is {score:f}"))
def entry_score(context: dict[str, Any], score: float) -> None:
    assert context["entry"]["score"] == score


@then(parsers.parse('the operator "{signal_type}" has precision {expected:f}'))
def operator_precision(context: dict[str, Any], signal_type: str, expected: float) -> None:
    matches = [r for r in context["report"] if r["signal_type"] == signal_type]
    assert matches, f"operator {signal_type} not found in the precision report"
    assert matches[0]["precision"] == expected
