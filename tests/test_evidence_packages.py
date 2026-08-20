"""Tests for the reproducible signal evidence packages (O9).

Responsibility: Validate package build, storage (metadata and
content addressing) and reproduction, with an in-memory storage.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from capiba.evidence import packages


class FakeStorage:
    """In-memory stand-in for EvidenceStorage (no MinIO)."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, Any]]] = {}

    def store(
        self,
        data: bytes,
        filename: str,
        metadata: dict[str, Any],
        content_type: str | None = None,
    ) -> dict[str, Any]:
        import hashlib

        sha256 = hashlib.sha256(data).hexdigest()
        self.objects[sha256] = (data, metadata)
        return {
            "sha256": sha256,
            "bucket": "fake",
            "object_name": f"evidence/document/detect/2026/08/{sha256}.json",
            "type": "document",
            "size_bytes": len(data),
            "timestamp": "2026-08-19T00:00:00+00:00",
        }


def _contract(
    contract_id: str,
    supplier: str = "12345678000199",
    modality: str = "dispensa",
    amount: float = 1000.0,
) -> dict[str, Any]:
    """Silver-shaped contract row."""
    return {
        "id": contract_id,
        "amount": amount,
        "signature_date": "2026-01-10",
        "validity_start": "2026-01-10",
        "validity_end": "2026-07-10",
        "buyer": {"siafi_code": "26000", "name": "Agency"},
        "supplier": {"cnpj": supplier, "legal_name": "ACME"},
        "modality": modality,
    }


SIGNAL = {
    "entity_type": "supplier",
    "entity_id": "12345678000199",
    "signal_type": "single_bid",
    "score": 1.0,
    "details": '{"contracts": 4, "non_competitive": 4}',
}
SIGNAL_KEY = "supplier:12345678000199:single_bid"


@pytest.fixture
def contracts() -> list[dict[str, Any]]:
    """Fixture: four dispensa contracts of the same supplier."""
    return [_contract(f"c{i}", amount=1000.0 * (i + 1)) for i in range(4)]


class TestBuildBatchPackage:
    """Tests for the batch package payload."""

    def test_payload_shape(self, contracts: list[dict[str, Any]]) -> None:
        """The package carries schema, window, code, signals and row hash."""
        package = packages.build_batch_package(contracts, [SIGNAL], date(2026, 1, 1))

        assert package["schema"] == "capiba.signal-package/1"
        assert package["kind"] == "batch"
        assert package["window"] == {"run_date": "2026-01-01"}
        assert package["code"]["package"] == "capiba"
        assert package["reproduction"]["operator"] == "detect_fraud_signals"
        assert package["signals"][0]["signal_type"] == "single_bid"
        assert package["source_rows"] == contracts
        assert len(package["source_rows_sha256"]) == 64

    def test_hash_is_order_insensitive(self, contracts: list[dict[str, Any]]) -> None:
        """Canonical hashing does not depend on key order."""
        shuffled = [dict(reversed(list(row.items()))) for row in contracts]
        assert packages._sha256(shuffled) == packages._sha256(contracts)

    def test_adhoc_run_has_null_window(self, contracts: list[dict[str, Any]]) -> None:
        """Without a run date the window is null."""
        package = packages.build_batch_package(contracts, [], None)
        assert package["window"] == {"run_date": None}


class TestStoreSignalPackages:
    """Tests for the storage of batch package + manifests."""

    def test_no_signals_stores_nothing(self, contracts: list[dict[str, Any]]) -> None:
        """A run without signals stores no package."""
        storage = FakeStorage()
        result = packages.store_signal_packages(storage, [], contracts, None)

        assert result == {"batch_sha256": None, "graph_sha256": None, "manifests": 0}
        assert storage.objects == {}

    def test_stores_batch_and_manifest(
        self, contracts: list[dict[str, Any]]
    ) -> None:
        """One batch package plus one manifest per signal are stored."""
        storage = FakeStorage()
        result = packages.store_signal_packages(
            storage, [SIGNAL], contracts, date(2026, 1, 1)
        )

        assert result["manifests"] == 1
        assert len(storage.objects) == 2

        batch_data, batch_meta = storage.objects[result["batch_sha256"]]
        assert batch_meta["signal_key"] == "batch:2026-01-01"
        assert "contract_id" not in batch_meta  # replaced by signal_key

        manifests = [
            (data, meta)
            for data, meta in storage.objects.values()
            if meta.get("signal_key") == SIGNAL_KEY
        ]
        assert len(manifests) == 1
        manifest = json.loads(manifests[0][0])
        assert manifest["signal_key"] == SIGNAL_KEY
        assert manifest["batch_sha256"] == result["batch_sha256"]
        assert manifest["reproducible"] is True
        assert manifests[0][1]["batch_sha256"] == result["batch_sha256"]

    def test_collusion_manifest_is_not_reproducible(self) -> None:
        """Graph-derived signals without a snapshot stay non-reproducible."""
        storage = FakeStorage()
        collusion = {
            "entity_type": "supplier",
            "entity_id": "91000000000001+91000000000002",
            "signal_type": "collusion_network",
            "score": 1.0,
            "details": "{}",
        }
        packages.store_signal_packages(storage, [collusion], [], None)

        manifest = next(
            json.loads(data)
            for data, meta in storage.objects.values()
            if meta.get("signal_key") == "supplier:91000000000001+91000000000002:collusion_network"
        )
        assert manifest["reproducible"] is False
        assert "graph_sha256" not in manifest


COLLUSION_SIGNAL = {
    "entity_type": "supplier",
    "entity_id": "91000000000001+91000000000002",
    "signal_type": "collusion_network",
    "score": 1.0,
    "details": '{"min_wins": 3, "suppliers": ["91000000000001", "91000000000002"]}',
}
COLLUSION_SIGNAL_KEY = "supplier:91000000000001+91000000000002:collusion_network"
SNAPSHOT_ROWS = [
    {"buyer": "B1", "supplier": "91000000000001", "wins": 4},
    {"buyer": "B1", "supplier": "91000000000002", "wins": 3},
]


class TestGraphBatchPackage:
    """Tests for the graph batch package (PR-D-03 eligibility snapshot)."""

    def test_payload_shape(self) -> None:
        """The package carries schema, min_wins, snapshot rows and hash."""
        package = packages.build_graph_batch_package(
            SNAPSHOT_ROWS, [COLLUSION_SIGNAL], 3, date(2026, 1, 1)
        )

        assert package["schema"] == "capiba.signal-package/1"
        assert package["kind"] == "graph_batch"
        assert package["window"] == {"run_date": "2026-01-01"}
        assert package["reproduction"] == {
            "operator": "detect_collusion",
            "min_wins": 3,
            "min_buyers": 1,
        }
        assert package["snapshot_rows"] == SNAPSHOT_ROWS
        assert len(package["snapshot_sha256"]) == 64

    def test_store_with_snapshot_makes_collusion_reproducible(self) -> None:
        """With a graph snapshot the collusion manifest is reproducible."""
        storage = FakeStorage()
        result = packages.store_signal_packages(
            storage,
            [COLLUSION_SIGNAL],
            [],
            date(2026, 1, 1),
            graph_snapshot={"rows": SNAPSHOT_ROWS, "min_wins": 3},
        )

        assert result["graph_sha256"] is not None
        graph_data, graph_meta = storage.objects[result["graph_sha256"]]
        assert graph_meta["signal_key"] == "graph-batch:2026-01-01"
        assert json.loads(graph_data)["kind"] == "graph_batch"

        manifest = next(
            json.loads(data)
            for data, meta in storage.objects.values()
            if meta.get("signal_key") == COLLUSION_SIGNAL_KEY
        )
        assert manifest["reproducible"] is True
        assert manifest["graph_sha256"] == result["graph_sha256"]

    def test_snapshot_rows_sorted_on_build(self) -> None:
        """Snapshot rows are stored sorted by (buyer, supplier)."""
        shuffled = list(reversed(SNAPSHOT_ROWS))
        package = packages.build_graph_batch_package(
            shuffled, [COLLUSION_SIGNAL], 3, None
        )
        assert package["snapshot_rows"] == SNAPSHOT_ROWS


class TestReproduceGraphSignal:
    """Tests for the reproduction of a collusion signal from its package."""

    def _package(self) -> dict[str, Any]:
        return packages.build_graph_batch_package(
            SNAPSHOT_ROWS, [COLLUSION_SIGNAL], 3, None
        )

    def test_reproduction_matches(self) -> None:
        """Same snapshot + same min_wins reproduce the stored signal."""
        outcome = packages.reproduce_signal(self._package(), COLLUSION_SIGNAL_KEY)

        assert outcome["integrity"] is True
        assert outcome["expected"] == 1.0
        assert outcome["actual"] == 1.0
        assert outcome["match"] is True

    def test_tampered_row_breaks_reproduction(self) -> None:
        """Removing a snapshot row fails integrity and the match."""
        package = self._package()
        package["snapshot_rows"] = package["snapshot_rows"][1:]

        outcome = packages.reproduce_signal(package, COLLUSION_SIGNAL_KEY)

        assert outcome["integrity"] is False
        assert outcome["match"] is False

    def test_pair_below_min_wins_does_not_reappear(self) -> None:
        """A snapshot with wins below min_wins yields no pair (actual None)."""
        rows = [dict(row, wins=2) for row in SNAPSHOT_ROWS]
        package = packages.build_graph_batch_package(rows, [COLLUSION_SIGNAL], 3, None)

        outcome = packages.reproduce_signal(package, COLLUSION_SIGNAL_KEY)

        assert outcome["integrity"] is True  # hash matches the stored rows
        assert outcome["expected"] == 1.0
        assert outcome["actual"] is None
        assert outcome["match"] is False

    def test_min_buyers_two_reproduction_matches(self) -> None:
        """PR-D-03b: a package at min_buyers=2 reproduces the refined pair."""
        rows = [
            {"buyer": "B1", "supplier": "91000000000001", "wins": 4},
            {"buyer": "B1", "supplier": "91000000000002", "wins": 3},
            {"buyer": "B2", "supplier": "91000000000001", "wins": 3},
            {"buyer": "B2", "supplier": "91000000000002", "wins": 5},
        ]
        package = packages.build_graph_batch_package(
            rows, [COLLUSION_SIGNAL], 3, None, min_buyers=2
        )

        outcome = packages.reproduce_signal(package, COLLUSION_SIGNAL_KEY)

        assert package["reproduction"]["min_buyers"] == 2
        assert outcome["integrity"] is True
        assert outcome["actual"] == 1.0
        assert outcome["match"] is True

    def test_min_buyers_two_requires_two_buyers(self) -> None:
        """A pair eligible in a single buyer does not reappear at min_buyers=2."""
        package = packages.build_graph_batch_package(
            SNAPSHOT_ROWS, [COLLUSION_SIGNAL], 3, None, min_buyers=2
        )

        outcome = packages.reproduce_signal(package, COLLUSION_SIGNAL_KEY)

        assert outcome["integrity"] is True
        assert outcome["actual"] is None
        assert outcome["match"] is False

    def test_legacy_package_without_min_buyers_uses_default_one(self) -> None:
        """Packages written before PR-D-03b reproduce with min_buyers=1."""
        package = self._package()
        del package["reproduction"]["min_buyers"]

        outcome = packages.reproduce_signal(package, COLLUSION_SIGNAL_KEY)

        assert outcome["integrity"] is True
        assert outcome["actual"] == 1.0
        assert outcome["match"] is True


class TestReproduceSignal:
    """Tests for the reproduction of a signal from its package."""

    def _package(self, contracts: list[dict[str, Any]]) -> dict[str, Any]:
        from capiba.pipeline.tasks import detect_fraud_signals

        signals = detect_fraud_signals(contracts)
        return packages.build_batch_package(contracts, signals, None)

    def test_reproduction_matches(self, contracts: list[dict[str, Any]]) -> None:
        """Same code + same rows reproduce the stored score."""
        outcome = packages.reproduce_signal(self._package(contracts), SIGNAL_KEY)

        assert outcome["integrity"] is True
        assert outcome["expected"] == 1.0
        assert outcome["actual"] == 1.0
        assert outcome["match"] is True

    def test_tampered_row_breaks_reproduction(
        self, contracts: list[dict[str, Any]]
    ) -> None:
        """A tampered source row fails the integrity check and the score."""
        package = self._package(contracts)
        package["source_rows"][0]["modality"] = "pregao"

        outcome = packages.reproduce_signal(package, SIGNAL_KEY)

        assert outcome["integrity"] is False
        assert outcome["match"] is False

    def test_unknown_signal_key(self, contracts: list[dict[str, Any]]) -> None:
        """A signal absent from the package yields expected None."""
        outcome = packages.reproduce_signal(
            self._package(contracts), "supplier:0:single_bid"
        )

        assert outcome["expected"] is None
        assert outcome["match"] is False
