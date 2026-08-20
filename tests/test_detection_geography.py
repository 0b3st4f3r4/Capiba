"""Tests for the anomalous geography signal (O6, PR-D-09).

Responsibility: Validate the declared semantics of
``capiba.detection.geography`` case by case (the G1-G10 structure of
PR-D-09 section 4) plus the edges (strict gate, aggregation, matriz
preference, determinism under shuffle) — no external infrastructure.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from capiba.detection.geography import (
    anomalous_geography_signals,
    haversine_km,
)

# Municipality seats with real coordinates (the battery anchors).
RECIFE = {"name": "Recife", "uf": "PE", "ibge_code": "2611606",
          "latitude": -8.0476, "longitude": -34.8770}
OLINDA = {"name": "Olinda", "uf": "PE", "ibge_code": "2609400",
          "latitude": -8.0089, "longitude": -34.8553}
JOAO_PESSOA = {"name": "João Pessoa", "uf": "PB", "ibge_code": "2507507",
               "latitude": -7.1195, "longitude": -34.8450}
SAO_PAULO = {"name": "São Paulo", "uf": "SP", "ibge_code": "3550308",
             "latitude": -23.5505, "longitude": -46.6333}

CNPJ = "12345678000199"

RFB = [
    {"tom_code": "2531", "name": "RECIFE"},
    {"tom_code": "6172", "name": "OLINDA"},
    {"tom_code": "2051", "name": "JOAO PESSOA"},
    {"tom_code": "7107", "name": "SAO PAULO"},
]


def _establishment(
    cnpj: str = CNPJ, tom: str = "2531", uf: str = "PE", matriz: bool = True
) -> dict[str, Any]:
    return {"cnpj": cnpj, "municipio": tom, "uf": uf, "is_matriz": matriz}


def _contract(
    city: str,
    uf: str,
    cnpj: str | None = CNPJ,
    cpf: str | None = None,
    amount: float = 10_000.0,
    contract_id: str = "C-1",
) -> dict[str, Any]:
    supplier: dict[str, Any] = {"cnpj": cnpj, "cpf": cpf}
    return {
        "id": contract_id,
        "buyer": {"siafi_code": "2531", "name": f"PREFEITURA DE {city}",
                  "city": city, "uf": uf},
        "supplier": supplier,
        "amount": amount,
        "signature_date": "2026-01-10",
    }


def _run(
    contracts: list[dict[str, Any]],
    establishments: list[dict[str, Any]] | None = None,
    municipalities: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    return anomalous_geography_signals(
        contracts,
        establishments if establishments is not None else [_establishment()],
        RFB,
        municipalities
        if municipalities is not None
        else [RECIFE, OLINDA, JOAO_PESSOA, SAO_PAULO],
        **kwargs,
    )


class TestHaversine:
    """The declared formula reproduces the pre-registered anchors."""

    @pytest.mark.parametrize(
        ("point_a", "point_b", "expected_km"),
        [
            ((-8.0476, -34.8770), (-8.0089, -34.8553), 4.922050),  # G2
            ((0.0, 0.0), (0.0, 0.75), 83.396195),  # G3
            ((-8.0476, -34.8770), (-7.1195, -34.8450), 103.260266),  # G4
            ((0.0, 0.0), (0.0, 1.0), 111.194927),  # G5
            ((0.0, 0.0), (0.0, 4.5), 500.377170),  # G6
            ((-8.0476, -34.8770), (-23.5505, -46.6333), 2131.060660),  # G7
        ],
    )
    def test_anchors(
        self, point_a: tuple[float, float], point_b: tuple[float, float],
        expected_km: float,
    ) -> None:
        assert abs(haversine_km(*point_a, *point_b) - expected_km) <= 1e-9 + 5e-7

    def test_same_point_is_zero(self) -> None:
        assert haversine_km(-8.0476, -34.8770, -8.0476, -34.8770) == 0.0


class TestSignalSemantics:
    """The G1-G10 disciplines of PR-D-09 section 4, case by case."""

    def test_g1_same_municipality_never_signals(self) -> None:
        signals = _run([_contract("Recife", "PE")])
        assert signals == []

    def test_g2_recife_olinda_below_gate(self) -> None:
        # Supplier in Recife, buyer in Olinda: 4.922050 km < 100.
        signals = _run([_contract("Olinda", "PE")])
        assert signals == []

    def test_g4_recife_joao_pessoa_signals_with_anchor_score(self) -> None:
        signals = _run([_contract("João Pessoa", "PB")])
        assert len(signals) == 1
        signal = signals[0]
        assert signal["entity_type"] == "supplier"
        assert signal["entity_id"] == CNPJ
        assert signal["signal_type"] == "anomalous_geography"
        assert signal["score"] == 0.1033
        details = json.loads(signal["details"])
        assert details["distance_km"] == 103.260266
        assert details["supplier_city"] == "Recife"
        assert details["supplier_ibge_code"] == "2611606"
        assert details["buyer_city"] == "João Pessoa"
        assert details["buyer_ibge_code"] == "2507507"
        assert details["contracts"] == 1
        assert details["contracts_total_brl"] == 10_000.0
        assert details["max_distance_km"] == 100.0

    def test_g7_recife_sao_paulo_saturates_score(self) -> None:
        # Supplier in São Paulo, buyer in Recife: 2131.060660 km -> 1.0.
        signals = _run(
            [_contract("Recife", "PE")],
            establishments=[_establishment(tom="7107", uf="SP")],
        )
        assert len(signals) == 1
        assert signals[0]["score"] == 1.0

    def test_g8_supplier_without_establishment_never_signals(self) -> None:
        signals = _run([_contract("São Paulo", "SP")], establishments=[])
        assert signals == []

    def test_g8_supplier_with_unknown_tom_never_signals(self) -> None:
        signals = _run(
            [_contract("São Paulo", "SP")],
            establishments=[_establishment(tom="9999")],
        )
        assert signals == []

    def test_g9_buyer_outside_the_table_never_signals(self) -> None:
        signals = _run([_contract("Cidade Inexistente", "PE")])
        assert signals == []

    def test_g10_individual_supplier_never_signals(self) -> None:
        contract = _contract("São Paulo", "SP", cnpj=None, cpf="12345678901")
        assert _run([contract]) == []

    def test_strict_gate_at_exactly_the_threshold(self) -> None:
        """distance == max_distance_km does not signal (strict >)."""
        contract = _contract("João Pessoa", "PB")
        threshold = haversine_km(-8.0476, -34.8770, -7.1195, -34.8450)
        assert _run([contract], max_distance_km=threshold) == []
        assert len(_run([contract], max_distance_km=threshold - 0.001)) == 1


class TestAggregation:
    """One signal per (supplier, buyer municipality) with count and sum."""

    def test_contracts_of_the_pair_aggregate(self) -> None:
        contracts = [
            _contract("João Pessoa", "PB", amount=10_000.0, contract_id="C-1"),
            _contract("joão pessoa", "pb", amount=2_500.0, contract_id="C-2"),
        ]
        signals = _run(contracts)
        assert len(signals) == 1
        details = json.loads(signals[0]["details"])
        assert details["contracts"] == 2
        assert details["contracts_total_brl"] == 12_500.0

    def test_same_supplier_two_buyers_emits_two_signals(self) -> None:
        contracts = [
            _contract("João Pessoa", "PB", contract_id="C-1"),
            _contract("São Paulo", "SP", contract_id="C-2"),
        ]
        signals = _run(contracts)
        assert len(signals) == 2
        buyers = {json.loads(s["details"])["buyer_ibge_code"] for s in signals}
        assert buyers == {"2507507", "3550308"}

    def test_matriz_wins_over_branch(self) -> None:
        establishments = [
            _establishment(tom="2531", uf="PE", matriz=False),  # branch first
            _establishment(tom="7107", uf="SP", matriz=True),
        ]
        # Matriz in São Paulo vs buyer Recife -> 2131 km -> signal.
        signals = _run([_contract("Recife", "PE")], establishments=establishments)
        assert len(signals) == 1
        assert json.loads(signals[0]["details"])["supplier_city"] == "São Paulo"


class TestDeterminism:
    """The signal set is invariant under input ordering."""

    def test_shuffled_inputs_bit_for_bit(self) -> None:
        contracts = [
            _contract("João Pessoa", "PB", contract_id="C-1"),
            _contract("São Paulo", "SP", contract_id="C-2"),
            _contract("Olinda", "PE", contract_id="C-3"),
        ]
        municipalities = [RECIFE, OLINDA, JOAO_PESSOA, SAO_PAULO]
        forward = _run(contracts, municipalities=municipalities)
        shuffled = _run(
            list(reversed(contracts)),
            municipalities=list(reversed(municipalities)),
        )
        assert forward == shuffled
