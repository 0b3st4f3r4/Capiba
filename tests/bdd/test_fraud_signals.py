"""BDD step definitions for batch fraud signal detection.

Feature file: tests/bdd/features/fraud_signals.feature
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.pipeline.tasks import detect_fraud_signals

scenarios("features/fraud_signals.feature")


def _contract(
    contract_id: str,
    amount: float,
    supplier: str,
    buyer: str,
    validity_end: str = "2026-07-10",
) -> dict[str, Any]:
    """Builds a silver-shaped contract row for the scenarios."""
    return {
        "id": contract_id,
        "amount": amount,
        "signature_date": "2026-01-10",
        "validity_start": "2026-01-10",
        "validity_end": validity_end,
        "buyer": {"siafi_code": buyer, "name": "Agency"},
        "supplier": {"cnpj": supplier, "legal_name": "ACME"},
    }


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (contracts in, signals out)."""
    return {}


@given(
    parsers.parse(
        '{count:d} contracts of supplier "{cnpj}" with leading digit 9 in all amounts'
    )
)
def contracts_benford_deviating(context: dict[str, Any], count: int, cnpj: str) -> None:
    context["contracts"] = [
        _contract(f"c{i}", 9000.0 + i, cnpj, "26000") for i in range(count)
    ]


@given(
    parsers.parse('{count:d} contracts of buyer "{buyer}" all won by the same supplier')
)
def contracts_single_supplier(context: dict[str, Any], count: int, buyer: str) -> None:
    context["contracts"] = [
        _contract(f"c{i}", 1000.0 * (i + 1), "12345678000199", buyer)
        for i in range(count)
    ]


@given(
    parsers.parse(
        '{count:d} contracts of supplier "{cnpj}"'
        " where one lasts 10 years and the rest 1 month"
    )
)
def contracts_duration_outlier(context: dict[str, Any], count: int, cnpj: str) -> None:
    context["contracts"] = [
        _contract(
            f"c{i}",
            1000.0 + i,
            cnpj,
            "26000",
            validity_end="2036-01-10" if i == 0 else "2026-02-10",
        )
        for i in range(count)
    ]


@given(
    parsers.parse('{count:d} contracts of supplier "{cnpj}" in modality "{modality}"')
)
def contracts_in_modality(
    context: dict[str, Any], count: int, cnpj: str, modality: str
) -> None:
    context["contracts"] = [
        {**_contract(f"c{i}", 1000.0 * (i + 1), cnpj, "26000"), "modality": modality}
        for i in range(count)
    ]


@when("the fraud signals are computed")
def compute(context: dict[str, Any]) -> None:
    context["signals"] = detect_fraud_signals(context["contracts"])


@then(
    parsers.parse('a "{signal_type}" signal is emitted for {entity_type} "{entity_id}"')
)
def signal_emitted(
    context: dict[str, Any], signal_type: str, entity_type: str, entity_id: str
) -> None:
    matches = [
        s
        for s in context["signals"]
        if s["signal_type"] == signal_type
        and s["entity_type"] == entity_type
        and s["entity_id"] == entity_id
    ]
    assert matches, f"signal {signal_type} for {entity_type} {entity_id} not found"
    context["signal"] = matches[0]


@then(parsers.parse('the "{signal_type}" signal score is above {threshold:f}'))
def score_above(context: dict[str, Any], signal_type: str, threshold: float) -> None:
    assert context["signal"]["signal_type"] == signal_type
    assert context["signal"]["score"] > threshold


@then(parsers.parse('the "{signal_type}" signal score is exactly {expected:f}'))
def score_exactly(context: dict[str, Any], signal_type: str, expected: float) -> None:
    assert context["signal"]["signal_type"] == signal_type
    assert context["signal"]["score"] == expected
