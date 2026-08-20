"""Tests for the fuzzy sanction screening (battery D-06b, PR-D-06b).

Responsibility: Guard the declared semantics of
``capiba.detection.screening_fuzzy`` — document veto, doc-assisted and
name-only regimes with their pre-registered thresholds, vigence at the
signature date, factual priority over the exact-match signal and
deterministic output.
"""

from __future__ import annotations

from typing import Any

from capiba.detection.screening_fuzzy import (
    fuzzy_match_score,
    sanctioned_name_match_signals,
)
from capiba.detection.signals import SignalType

MASKED = "***435151**"
FULL_CPF = "12343515100"  # contains the visible digits 435151
OTHER_CPF = "99900011122"


def _sanction(
    sanction_id: str = "ceaf-1",
    name: str = "MARIA DE FATIMA PEREIRA",
    list_name: str = "ceaf",
    masked: str | None = MASKED,
    cpf: str | None = None,
    start: str = "2025-01-01",
    end: str | None = None,
) -> dict[str, Any]:
    return {
        "id": sanction_id,
        "list_name": list_name,
        "sanctioned_name": name,
        "masked_document": masked,
        "cnpj": None,
        "cpf": cpf,
        "start_date": start,
        "end_date": end,
    }


def _contract(
    contract_id: str = "C1",
    name: str = "MARIA DE FATIMA PEREIRA",
    cpf: str | None = FULL_CPF,
    signed: str = "2026-03-10",
) -> dict[str, Any]:
    supplier: dict[str, Any] = {"legal_name": name}
    if cpf:
        supplier["cpf"] = cpf
    return {"id": contract_id, "supplier": supplier, "signature_date": signed}


class TestFuzzyMatchScore:
    """Pair-level semantics: veto, regimes and thresholds."""

    def test_doc_assisted_identical_name(self) -> None:
        """Masked doc + identical name scores 1.0 (F1)."""
        score = fuzzy_match_score(_sanction(), _contract()["supplier"])
        assert score == 1.0

    def test_doc_assisted_noisy_name(self) -> None:
        """Accent/case/order noise with a compatible masked doc signals (F2)."""
        score = fuzzy_match_score(
            _sanction(), _contract(name="Pereira, Maria de Fátima")["supplier"]
        )
        assert score is not None
        assert score >= 0.85

    def test_contradictory_masked_document_vetoes(self) -> None:
        """Identical name with divergent masked digits never signals (F3)."""
        score = fuzzy_match_score(
            _sanction(masked="***999888**"), _contract()["supplier"]
        )
        assert score is None

    def test_compatible_masked_doc_disjoint_names(self) -> None:
        """Same masked doc with disjoint names stays under the threshold (F4)."""
        score = fuzzy_match_score(
            _sanction(), _contract(name="JORGE HENRIQUE AMORIM")["supplier"]
        )
        assert score is None

    def test_name_only_identical(self) -> None:
        """Without document evidence, an identical name signals at 1.0 (F5)."""
        score = fuzzy_match_score(
            _sanction(masked=None), _contract(cpf=None)["supplier"]
        )
        assert score == 1.0

    def test_name_only_below_high_threshold(self) -> None:
        """A 0.88 name-only similarity stays under the 0.95 threshold (F6)."""
        score = fuzzy_match_score(
            _sanction(masked=None),
            _contract(name="MARIA DE FATIMA PEREIRA SOUZA", cpf=None)["supplier"],
        )
        assert score is None

    def test_full_document_contradiction_vetoes(self) -> None:
        """A CEIS full document different from the supplier's vetoes (F7)."""
        score = fuzzy_match_score(
            _sanction(list_name="ceis", masked=None, cpf=OTHER_CPF),
            _contract()["supplier"],
        )
        assert score is None

    def test_missing_supplier_document_is_not_contradiction(self) -> None:
        """A documentless supplier falls back to the name-only regime."""
        score = fuzzy_match_score(
            _sanction(masked=None, cpf=OTHER_CPF), _contract(cpf=None)["supplier"]
        )
        assert score == 1.0


class TestSanctionedNameMatchSignals:
    """Signal-level semantics: vigence, priority, composition, determinism."""

    def test_emits_per_supplier_and_list(self) -> None:
        """One signal per supplier × list, score and details composed."""
        signals = sanctioned_name_match_signals(
            [_contract("C1"), _contract("C2")], [_sanction()]
        )

        assert len(signals) == 1
        signal = signals[0]
        assert signal["signal_type"] == SignalType.SANCTIONED_NAME_MATCH
        assert signal["entity_id"] == FULL_CPF
        assert signal["score"] == 1.0
        assert '"contracts": 2' in signal["details"]
        assert '"ceaf-1"' in signal["details"]

    def test_not_vigent_at_signature(self) -> None:
        """A sanction outside the signature date never signals (F8)."""
        signals = sanctioned_name_match_signals(
            [_contract(signed="2024-12-31")], [_sanction(start="2025-01-01")]
        )
        assert signals == []

    def test_exact_match_priority(self) -> None:
        """An exact document match on the sanction suppresses the fuzzy (F9)."""
        signals = sanctioned_name_match_signals(
            [_contract()], [_sanction(masked=None, cpf=FULL_CPF)]
        )
        assert signals == []

    def test_control_supplier_without_sanction(self) -> None:
        """Suppliers without any matching sanction never signal."""
        signals = sanctioned_name_match_signals(
            [_contract(name="EMPRESA QUALQUER LTDA", cpf="11122233344")],
            [_sanction()],
        )
        assert signals == []

    def test_deterministic_output(self) -> None:
        """Same input, same signals, bit for bit."""
        contracts = [_contract("C2"), _contract("C1")]
        first = sanctioned_name_match_signals(contracts, [_sanction()])
        second = sanctioned_name_match_signals(contracts, [_sanction()])
        assert first == second
