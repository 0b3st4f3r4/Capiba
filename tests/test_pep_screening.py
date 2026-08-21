"""Unit tests for the PEP screening yente adapter (bateria D-12).

Responsibility: Validate the pure FtM query construction and the response
reduction of ``capiba.detection.pep_screening`` with a stubbed yente
client (no service, no network), per PR-D-12 § 3. The battery regime
(P2, 5 seeds) lives in ``tests/test_detect_battery_pep_screening.py``.
"""

from __future__ import annotations

import json
from typing import Any

from capiba.detection.pep_screening import (
    build_match_query,
    pep_supplier_match_signals,
    reduce_candidates,
)
from capiba.detection.signals import SignalType


def test_query_pf_with_cpf_is_full_ftm_person() -> None:
    """Q1: name + idNumber + nationality=br, exactly."""
    query = build_match_query({"legal_name": "MARIA SILVA", "cpf": "12345678901"})
    assert query == {
        "schema": "Person",
        "properties": {
            "name": ["MARIA SILVA"],
            "idNumber": ["12345678901"],
            "nationality": ["br"],
        },
    }


def test_query_pf_without_cpf_is_name_only() -> None:
    """Q2: no idNumber key at all."""
    query = build_match_query({"legal_name": "JOSE SANTOS"})
    assert query == {
        "schema": "Person",
        "properties": {"name": ["JOSE SANTOS"], "nationality": ["br"]},
    }


def test_query_company_never_consults() -> None:
    """Q3: a company is not a PEP."""
    assert build_match_query({"legal_name": "EMPRESA LTDA", "cnpj": "1" * 14}) is None


def test_query_nameless_never_consults() -> None:
    """Q4: a nameless query would match everything."""
    assert build_match_query({"cpf": "12345678901"}) is None


def test_reduce_two_candidates_above_threshold() -> None:
    """Q5: one signal, score = best candidate, details with both ids."""
    query = build_match_query({"legal_name": "ANA", "cpf": "1"})
    assert query is not None
    candidates = [
        {"id": "os-1", "score": 0.72, "properties": {"name": ["Ana Um"]}},
        {"id": "os-2", "score": 0.91, "properties": {"name": ["Ana Dois"]}},
    ]
    signal = reduce_candidates(query, "1", candidates)
    assert signal is not None
    assert signal["signal_type"] == SignalType.PEP_SUPPLIER_MATCH
    assert signal["score"] == 0.91
    details = json.loads(signal["details"])
    assert [c["id"] for c in details["candidates"]] == ["os-2", "os-1"]
    assert details["query"] == query
    assert details["dataset"] == "br_pep"
    assert details["threshold"] == 0.7


def test_reduce_no_candidates_or_below_threshold() -> None:
    """Q6: empty response, or all candidates below the threshold."""
    query = build_match_query({"legal_name": "ANA"})
    assert query is not None
    assert reduce_candidates(query, "ANA", []) is None
    below = [{"id": "os-1", "score": 0.69, "properties": {}}]
    assert reduce_candidates(query, "ANA", below) is None


def test_signals_dedup_same_supplier_across_contracts() -> None:
    """Q7: the same supplier in N contracts queries once, signals at most once."""
    calls: list[dict[str, Any]] = []

    def stub(query: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(query)
        return [{"id": "os-1", "score": 0.8, "properties": {"name": ["Ana"]}}]

    contracts = [
        {"id": f"c-{i}", "supplier": {"legal_name": "ANA", "cpf": "1"}}
        for i in range(3)
    ]
    signals = pep_supplier_match_signals(contracts, stub)
    assert len(calls) == 1
    assert len(signals) == 1


def test_signals_sorted_by_entity_id() -> None:
    """Bit-for-bit determinism: signals come out sorted by entity id."""

    def stub(query: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"id": "os-1", "score": 0.8, "properties": {}}]

    contracts = [
        {"id": "c-1", "supplier": {"legal_name": "B", "cpf": "222"}},
        {"id": "c-2", "supplier": {"legal_name": "A", "cpf": "111"}},
    ]
    signals = pep_supplier_match_signals(contracts, stub)
    assert [s["entity_id"] for s in signals] == ["111", "222"]
