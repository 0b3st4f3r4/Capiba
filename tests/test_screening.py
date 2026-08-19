"""Unit tests for the sanction screening module (battery D-06 semantics).

The reference semantics is declared in docs/preregistrations/PR-D-06.md
(section 3): exact document match (CNPJ/CPF), sanction vigent at the
contract's signature date (inclusive bounds, open end when NULL), binary
score 1.0, one signal per supplier. A name match is NOT a match.
"""

from __future__ import annotations

import json

from capiba.detection.screening import sanctioned_supplier_signals
from capiba.detection.signals import SignalType


def _contract(
    contract_id: str,
    signature_date: str,
    cnpj: str | None = None,
    cpf: str | None = None,
    name: str = "Fornecedor",
) -> dict[str, object]:
    return {
        "id": contract_id,
        "signature_date": signature_date,
        "supplier": {"cnpj": cnpj, "cpf": cpf, "legal_name": name},
    }


def _sanction(
    sanction_id: str,
    start: str | None = "2026-01-01",
    end: str | None = "2026-12-31",
    cnpj: str | None = None,
    cpf: str | None = None,
    list_name: str = "ceis",
) -> dict[str, object]:
    return {
        "id": sanction_id,
        "list_name": list_name,
        "cnpj": cnpj,
        "cpf": cpf,
        "start_date": start,
        "end_date": end,
    }


CNPJ = "11111111000111"


class TestVigenceWindow:
    def test_s1_signature_inside_window_signals(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-06-15", cnpj=CNPJ)],
            [_sanction("S1", cnpj=CNPJ)],
        )
        assert len(signals) == 1
        assert signals[0]["entity_id"] == CNPJ
        assert signals[0]["entity_type"] == "supplier"
        assert signals[0]["signal_type"] == SignalType.SANCTIONED_SUPPLIER
        assert signals[0]["score"] == 1.0

    def test_s2_one_day_after_end_does_not_signal(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2027-01-01", cnpj=CNPJ)],
            [_sanction("S2", cnpj=CNPJ)],
        )
        assert signals == []

    def test_s3_signature_on_end_date_signals(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-12-31", cnpj=CNPJ)],
            [_sanction("S3", cnpj=CNPJ)],
        )
        assert len(signals) == 1

    def test_s4_one_day_before_start_does_not_signal(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2025-12-31", cnpj=CNPJ)],
            [_sanction("S4", cnpj=CNPJ)],
        )
        assert signals == []

    def test_s5_open_end_signals_after_start(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2030-05-10", cnpj=CNPJ)],
            [_sanction("S5", end=None, cnpj=CNPJ)],
        )
        assert len(signals) == 1

    def test_s10_missing_start_does_not_signal(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-06-15", cnpj=CNPJ)],
            [_sanction("S10", start=None, cnpj=CNPJ)],
        )
        assert signals == []


class TestDocumentDiscipline:
    def test_s6_cpf_match_signals(self) -> None:
        cpf = "12345678901"
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-06-15", cpf=cpf)],
            [_sanction("S6", cpf=cpf, list_name="cnep")],
        )
        assert len(signals) == 1
        assert signals[0]["entity_id"] == cpf

    def test_s7_same_name_different_document_does_not_signal(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-06-15", cnpj="99999999000199", name="ACME LTDA")],
            [_sanction("S7", cnpj=CNPJ)],
        )
        assert signals == []

    def test_s8_supplier_without_document_does_not_signal(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-06-15", cnpj=None, cpf=None, name="ACME LTDA")],
            [_sanction("S8", cnpj=CNPJ)],
        )
        assert signals == []

    def test_contract_without_signature_date_does_not_signal(self) -> None:
        signals = sanctioned_supplier_signals(
            [{"id": "C1", "signature_date": None, "supplier": {"cnpj": CNPJ}}],
            [_sanction("S1", cnpj=CNPJ)],
        )
        assert signals == []


class TestSignalComposition:
    def test_s9_only_the_vigent_sanction_in_details(self) -> None:
        signals = sanctioned_supplier_signals(
            [_contract("C1", "2026-06-15", cnpj=CNPJ)],
            [
                _sanction("S9-OLD", start="2020-01-01", end="2020-12-31", cnpj=CNPJ),
                _sanction("S9-CURRENT", cnpj=CNPJ),
            ],
        )
        assert len(signals) == 1
        details = json.loads(str(signals[0]["details"]))
        assert details["sanctions"] == ["S9-CURRENT"]
        assert details["lists"] == ["ceis"]
        assert details["contracts"] == 1

    def test_one_signal_per_supplier_with_contract_count(self) -> None:
        contracts = [
            _contract("C1", "2026-03-10", cnpj=CNPJ),
            _contract("C2", "2026-04-10", cnpj=CNPJ),
            _contract("C3", "2027-06-15", cnpj=CNPJ),  # after the end date
        ]
        signals = sanctioned_supplier_signals(contracts, [_sanction("S1", cnpj=CNPJ)])
        assert len(signals) == 1
        details = json.loads(str(signals[0]["details"]))
        assert details["contracts"] == 2

    def test_output_sorted_by_entity_id(self) -> None:
        other = "22222222000122"
        contracts = [
            _contract("C1", "2026-06-15", cnpj=other),
            _contract("C2", "2026-06-15", cnpj=CNPJ),
        ]
        sanctions = [_sanction("SA", cnpj=CNPJ), _sanction("SB", cnpj=other)]
        signals = sanctioned_supplier_signals(contracts, sanctions)
        assert [s["entity_id"] for s in signals] == sorted([CNPJ, other])

    def test_empty_inputs(self) -> None:
        assert sanctioned_supplier_signals([], []) == []
