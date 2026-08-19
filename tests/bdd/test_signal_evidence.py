"""BDD step definitions for the reproducible evidence packages (O9).

Feature file: tests/bdd/features/signal_evidence.feature
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.evidence import packages
from capiba.pipeline.tasks import detect_fraud_signals

scenarios("features/signal_evidence.feature")


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
            "object_name": f"evidence/document/detect/{sha256}.json",
            "type": "document",
            "size_bytes": len(data),
            "timestamp": "2026-08-19T00:00:00+00:00",
        }


def _contract(
    contract_id: str,
    amount: float,
    supplier: str,
    buyer: str,
    modality: str,
) -> dict[str, Any]:
    """Builds a silver-shaped contract row for the scenarios."""
    return {
        "id": contract_id,
        "amount": amount,
        "signature_date": "2026-01-10",
        "validity_start": "2026-01-10",
        "validity_end": "2026-07-10",
        "buyer": {"siafi_code": buyer, "name": "Agency"},
        "supplier": {"cnpj": supplier, "legal_name": "ACME"},
        "modality": modality,
    }


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (contracts in, packages out)."""
    return {"storage": FakeStorage()}


@given(
    parsers.parse('{count:d} contracts of supplier "{cnpj}" in modality "{modality}"')
)
def contracts_in_modality(
    context: dict[str, Any], count: int, cnpj: str, modality: str
) -> None:
    context["contracts"] = [
        _contract(f"c{i}", 1000.0 * (i + 1), cnpj, "26000", modality)
        for i in range(count)
    ]


@given(parsers.parse('a computed collusion signal "{signal_key}"'))
def collusion_signal(context: dict[str, Any], signal_key: str) -> None:
    context["contracts"] = []
    context["signals"] = [
        {
            "entity_type": "supplier",
            "entity_id": "91000000000001+91000000000002",
            "signal_type": "collusion_network",
            "score": 1.0,
            "details": json.dumps(
                {"min_wins": 3, "suppliers": ["91000000000001", "91000000000002"]}
            ),
        }
    ]
    context["signal_key"] = signal_key


@when("the fraud signals are computed")
def compute(context: dict[str, Any]) -> None:
    context["signals"] = detect_fraud_signals(context["contracts"])


@when("the evidence packages are stored")
def store(context: dict[str, Any]) -> None:
    context["result"] = packages.store_signal_packages(
        context["storage"],
        context["signals"],
        context.get("contracts", []),
        run_date=None,
    )
    batch_sha = context["result"]["batch_sha256"]
    data, _ = context["storage"].objects[batch_sha]
    context["batch_package"] = json.loads(data)


@when("a source row of the batch package is tampered with")
def tamper(context: dict[str, Any]) -> None:
    context["batch_package"]["source_rows"][0]["modality"] = "pregao"


@then(parsers.parse('the signal "{signal_key}" has a stored manifest'))
def manifest_stored(context: dict[str, Any], signal_key: str) -> None:
    manifests = [
        metadata
        for _, metadata in context["storage"].objects.values()
        if metadata.get("signal_key") == signal_key
    ]
    assert manifests, f"no stored manifest for {signal_key}"


@then(
    parsers.parse(
        'reproducing "{signal_key}" from the batch package matches the stored score'
    )
)
def reproduction_matches(context: dict[str, Any], signal_key: str) -> None:
    outcome = packages.reproduce_signal(context["batch_package"], signal_key)
    assert outcome["match"], outcome


@then(
    parsers.parse(
        'reproducing "{signal_key}" from the batch package'
        " does not match the stored score"
    )
)
def reproduction_diverges(context: dict[str, Any], signal_key: str) -> None:
    outcome = packages.reproduce_signal(context["batch_package"], signal_key)
    assert not outcome["match"], outcome


@then(parsers.parse('the manifest of "{signal_key}" is marked non-reproducible'))
def manifest_non_reproducible(context: dict[str, Any], signal_key: str) -> None:
    manifests = [
        json.loads(data)
        for data, metadata in context["storage"].objects.values()
        if metadata.get("signal_key") == signal_key
    ]
    assert manifests, f"no stored manifest for {signal_key}"
    assert manifests[0]["reproducible"] is False
