"""Tests for the editorial triage persistence (capiba.db.triage).

Responsibility: Validate registration idempotency, editorial
transitions and the precision report, with ArangoDB mocked.
"""

from __future__ import annotations

from typing import Any

import pytest

from capiba.db import triage
from capiba.db.triage import TriageError, TriageStatus


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


def _fake_aql(
    db: FakeDb, query: str, bind_vars: dict[str, Any] | None = None
) -> list[Any]:
    """Applies the listing/count AQL semantics (filters, sort, limit) to
    the fake collection, mirroring what ArangoDB would do server-side."""
    bind_vars = bind_vars or {}
    docs = list(db.col.docs.values())
    if "status" in bind_vars:
        docs = [d for d in docs if d.get("status") == bind_vars["status"]]
    if "signal_type" in bind_vars:
        docs = [d for d in docs if d.get("signal_type") == bind_vars["signal_type"]]
    if "min_score" in bind_vars:
        docs = [d for d in docs if (d.get("score") or 0) >= bind_vars["min_score"]]
    if "COLLECT WITH COUNT" in query:
        return [len(docs)]
    docs.sort(
        key=lambda d: (d.get("score") or 0, str(d.get("last_seen") or "")),
        reverse=True,
    )
    offset = bind_vars.get("offset", 0)
    limit = bind_vars.get("limit", len(docs))
    return docs[offset : offset + limit]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    """Fixture: fake db with AQL reads routed to the fake collection."""
    fake = FakeDb()
    monkeypatch.setattr(triage, "execute_aql", _fake_aql)
    return fake


def _signal(
    signal_type: str = "single_bid",
    entity_id: str = "12345678000199",
    score: float = 0.8,
) -> dict[str, Any]:
    """Detect-shaped signal row."""
    return {
        "entity_type": "supplier",
        "entity_id": entity_id,
        "signal_type": signal_type,
        "score": score,
        "details": "{}",
    }


class TestRegisterSignals:
    """Tests for the triage registration (insert-if-absent)."""

    def test_registers_as_pending_review(self, db: FakeDb) -> None:
        """New signals must enter the queue as pending_review."""
        assert triage.register_signals(db, [_signal()]) == 1

        doc = db.col.docs["supplier:12345678000199:single_bid"]
        assert doc["status"] == "pending_review"
        assert doc["score"] == 0.8
        assert doc["history"] == []

    def test_reregistration_preserves_review_and_refreshes_snapshot(
        self, db: FakeDb
    ) -> None:
        """Recomputation must not overwrite the editorial state."""
        triage.register_signals(db, [_signal(score=0.8)])
        key = triage.signal_key("supplier", "12345678000199", "single_bid")
        triage.apply_review(db, key, TriageStatus.CONFIRMED, "ana")

        assert triage.register_signals(db, [_signal(score=0.9)]) == 0

        doc = db.col.docs[key]
        assert doc["status"] == "confirmed"
        assert doc["score"] == 0.9

    def test_creates_collection_when_missing(
        self, db: FakeDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The collection is created lazily on first use."""
        created: list[str] = []
        monkeypatch.setattr(db, "has_collection", lambda name: False)
        monkeypatch.setattr(db, "create_collection", created.append)

        triage.register_signals(db, [_signal()])

        assert created == [triage.TRIAGE_COLLECTION]


class TestApplyReview:
    """Tests for the editorial transitions."""

    def _register(self, db: FakeDb) -> str:
        triage.register_signals(db, [_signal()])
        return triage.signal_key("supplier", "12345678000199", "single_bid")

    def test_confirm_requires_reviewer(self, db: FakeDb) -> None:
        """Every transition requires a reviewer."""
        key = self._register(db)
        with pytest.raises(TriageError, match="reviewer"):
            triage.apply_review(db, key, TriageStatus.CONFIRMED, " ")

    def test_reject_requires_reason(self, db: FakeDb) -> None:
        """Rejection without a reason must be refused."""
        key = self._register(db)
        with pytest.raises(TriageError, match="reason"):
            triage.apply_review(db, key, TriageStatus.REJECTED, "ana", reason=None)
        assert db.col.docs[key]["status"] == "pending_review"

    def test_reject_records_reason_and_history(self, db: FakeDb) -> None:
        """A rejection keeps reviewer, reason and an audit entry."""
        key = self._register(db)
        doc = triage.apply_review(
            db, key, TriageStatus.REJECTED, "ana", reason="falso positivo"
        )

        assert doc["status"] == "rejected"
        assert doc["reviewed_by"] == "ana"
        assert doc["reason"] == "falso positivo"
        assert doc["history"][-1]["status"] == "rejected"

    def test_published_is_terminal(self, db: FakeDb) -> None:
        """No transition is allowed out of published."""
        key = self._register(db)
        triage.apply_review(db, key, TriageStatus.CONFIRMED, "ana")
        triage.apply_review(db, key, TriageStatus.PUBLISHED, "ana")

        with pytest.raises(TriageError, match="invalid transition"):
            triage.apply_review(
                db, key, TriageStatus.REJECTED, "bruno", reason="tarde demais"
            )
        assert db.col.docs[key]["status"] == "published"

    def test_unknown_key_raises_key_error(self, db: FakeDb) -> None:
        """Reviewing an unregistered signal must raise KeyError."""
        with pytest.raises(KeyError):
            triage.apply_review(
                db, "supplier:0:single_bid", TriageStatus.CONFIRMED, "ana"
            )

    def test_rejected_can_be_reconfirmed(self, db: FakeDb) -> None:
        """Re-triage: a rejected signal can be confirmed later."""
        key = self._register(db)
        triage.apply_review(db, key, TriageStatus.REJECTED, "ana", reason="ruído")
        doc = triage.apply_review(db, key, TriageStatus.CONFIRMED, "bruno")

        assert doc["status"] == "confirmed"
        assert len(doc["history"]) == 2


class TestListReviews:
    """Tests for the triage listing."""

    def test_filters_by_status_and_signal_type(self, db: FakeDb) -> None:
        """Filters must narrow the listing."""
        triage.register_signals(
            db,
            [
                _signal(signal_type="single_bid", entity_id="1"),
                _signal(signal_type="concentration", entity_id="2"),
            ],
        )
        key = triage.signal_key("supplier", "1", "single_bid")
        triage.apply_review(db, key, TriageStatus.CONFIRMED, "ana")

        pending = triage.list_reviews(db, status=TriageStatus.PENDING_REVIEW)
        assert [d["signal_type"] for d in pending] == ["concentration"]

        single_bid = triage.list_reviews(db, signal_type="single_bid")
        assert [d["entity_id"] for d in single_bid] == ["1"]

    def test_limit_is_applied(self, db: FakeDb) -> None:
        """The listing respects the limit."""
        triage.register_signals(db, [_signal(entity_id=str(i)) for i in range(5)])
        assert len(triage.list_reviews(db, limit=2)) == 2

    def test_empty_when_collection_missing(
        self, db: FakeDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the collection the listing is empty."""
        monkeypatch.setattr(db, "has_collection", lambda name: False)
        assert triage.list_reviews(db) == []

    def test_score_first_ordering(self, db: FakeDb) -> None:
        """The listing is sorted by score desc, not insertion order."""
        triage.register_signals(
            db,
            [
                _signal(entity_id="1", score=0.5),
                _signal(entity_id="2", score=0.9),
                _signal(entity_id="3", score=0.7),
            ],
        )
        docs = triage.list_reviews(db)
        assert [d["entity_id"] for d in docs] == ["2", "3", "1"]

    def test_filters_by_min_score(self, db: FakeDb) -> None:
        """The min_score filter keeps only signals at or above it."""
        triage.register_signals(
            db,
            [
                _signal(entity_id="1", score=0.5),
                _signal(entity_id="2", score=0.9),
            ],
        )
        docs = triage.list_reviews(db, min_score=0.7)
        assert [d["entity_id"] for d in docs] == ["2"]

    def test_offset_paginates(self, db: FakeDb) -> None:
        """Offset + limit page over the sorted listing."""
        triage.register_signals(
            db, [_signal(entity_id=str(i), score=0.1 * i) for i in range(5)]
        )
        page1 = triage.list_reviews(db, limit=2)
        page2 = triage.list_reviews(db, limit=2, offset=2)
        page3 = triage.list_reviews(db, limit=2, offset=4)
        ids = [d["entity_id"] for d in page1 + page2 + page3]
        assert ids == ["4", "3", "2", "1", "0"]

    def test_defaults_preserve_previous_contract(self, db: FakeDb) -> None:
        """Without the new parameters the listing returns up to 100 docs."""
        triage.register_signals(db, [_signal(entity_id=str(i)) for i in range(120)])
        assert len(triage.list_reviews(db)) == 100


class TestCountReviews:
    """Tests for the server-side triage count (pagination total)."""

    def test_counts_with_filters(self, db: FakeDb) -> None:
        """The count respects the same filters as the listing."""
        triage.register_signals(
            db,
            [
                _signal(signal_type="single_bid", entity_id="1", score=0.9),
                _signal(signal_type="concentration", entity_id="2", score=0.5),
            ],
        )
        assert triage.count_reviews(db) == 2
        assert triage.count_reviews(db, signal_type="single_bid") == 1
        assert triage.count_reviews(db, min_score=0.7) == 1
        assert triage.count_reviews(db, status=TriageStatus.CONFIRMED) == 0

    def test_zero_when_collection_missing(
        self, db: FakeDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the collection the count is zero."""
        monkeypatch.setattr(db, "has_collection", lambda name: False)
        assert triage.count_reviews(db) == 0


class TestPrecisionReport:
    """Tests for the per-operator precision report."""

    def test_precision_aggregation(self, db: FakeDb) -> None:
        """Precision is confirmed / (confirmed + rejected), per operator."""
        triage.register_signals(
            db,
            [
                _signal(entity_id="1"),
                _signal(entity_id="2"),
                _signal(entity_id="3"),
                _signal(signal_type="concentration", entity_id="4"),
            ],
        )
        triage.apply_review(
            db, triage.signal_key("supplier", "1", "single_bid"),
            TriageStatus.CONFIRMED, "ana",
        )
        triage.apply_review(
            db, triage.signal_key("supplier", "2", "single_bid"),
            TriageStatus.REJECTED, "ana", reason="ruído",
        )

        report = {r["signal_type"]: r for r in triage.precision_report(db)}

        single_bid = report["single_bid"]
        assert single_bid["confirmed"] == 1
        assert single_bid["rejected"] == 1
        assert single_bid["pending_review"] == 1
        assert single_bid["precision"] == 0.5

        concentration = report["concentration"]
        assert concentration["pending_review"] == 1
        assert concentration["precision"] is None

    def test_empty_report(self, db: FakeDb) -> None:
        """No entries means an empty report."""
        assert triage.precision_report(db) == []
