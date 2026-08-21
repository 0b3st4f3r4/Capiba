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

    def test_prefilter_keeps_pairs_without_shared_tokens(self) -> None:
        # Regression guard for the character-multiset prefilter: the bound
        # is over character counts, not tokens, so a pair with high name
        # similarity but disjoint tokens (every token slightly mutated,
        # ratio ~0.846) still reaches fuzzy_match_score and emits — token
        # blocking would drop it. Doc-assisted regime: 0.6 * 0.8462 + 0.4.
        sanction = _sanction(name="AAAAAA BBBBBB")
        contract = _contract(name="AAAAAB BBBBBA")
        signals = sanctioned_name_match_signals([contract], [sanction])
        assert len(signals) == 1
        assert signals[0]["score"] == round(0.6 * 0.8461538461538461 + 0.4, 4)


def _naive_reference(
    contracts: list[dict[str, Any]], sanctions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Brute-force transcription of the PR-D-06b semantics (O(N·M)).

    Reference for the equivalence property test: the optimized
    ``sanctioned_name_match_signals`` (indexed by document, grouped by
    supplier) must produce exactly this output on any input.
    """
    import json

    from capiba.detection.screening import _as_date, _vigent_at

    def _doc(record: dict[str, Any]) -> str | None:
        document = record.get("cnpj") or record.get("cpf")
        return str(document) if document else None

    exact: set[tuple[str, str]] = set()
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        supplier_doc = _doc(supplier)
        if supplier_doc is None:
            continue
        for sanction in sanctions:
            if _doc(sanction) == supplier_doc:
                exact.add((supplier_doc, str(sanction["id"])))

    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        if not supplier.get("legal_name"):
            continue
        signed_on = _as_date(contract.get("signature_date"))
        if signed_on is None:
            continue
        entity_id = _doc(supplier) or str(supplier["legal_name"])
        for sanction in sanctions:
            if not _vigent_at(sanction, signed_on):
                continue
            if (entity_id, str(sanction["id"])) in exact:
                continue
            score = fuzzy_match_score(sanction, supplier)
            if score is None:
                continue
            key = (entity_id, str(sanction["list_name"]))
            hit = hits.setdefault(
                key, {"sanctions": set(), "contracts": set(), "score": 0.0}
            )
            hit["sanctions"].add(str(sanction["id"]))
            hit["contracts"].add(str(contract.get("id")))
            hit["score"] = max(hit["score"], score)

    return [
        {
            "entity_type": "supplier",
            "entity_id": entity_id,
            "signal_type": SignalType.SANCTIONED_NAME_MATCH,
            "score": round(hit["score"], 4),
            "details": json.dumps(
                {
                    "sanctions": sorted(hit["sanctions"]),
                    "lists": [list_name],
                    "contracts": len(hit["contracts"]),
                    "match": "fuzzy",
                },
                sort_keys=True,
            ),
        }
        for (entity_id, list_name), hit in sorted(hits.items())
    ]


class TestIndexedImplementationEquivalence:
    """The indexed implementation must equal the naive O(N·M) reference."""

    def _corpus(self, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        import random

        rng = random.Random(seed)
        names = [
            "MARIA DE FATIMA PEREIRA",
            "MARIA DE FATIMA PEREIRA SOUZA",
            "PEREIRA MARIA DE FATIMA",
            "JOSE RAIMUNDO SILVA",
            "EMPRESA QUALQUER LTDA",
            "EMPRESA QUALQUER LTDA ME",
            "JORGE HENRIQUE AMORIM",
        ]
        docs = ["12343515100", "99900011122", "11111111000111", None]
        sanctions = [
            _sanction(
                sanction_id=f"s-{i}",
                name=rng.choice(names),
                list_name=rng.choice(["ceaf", "ceis", "cnep"]),
                masked=rng.choice([MASKED, "***999888**", None]),
                cpf=rng.choice(docs),
                start=rng.choice(["2025-01-01", "2026-01-01"]),
                end=rng.choice([None, "2026-06-30"]),
            )
            for i in range(15)
        ]
        contracts = [
            _contract(
                f"C{i}",
                name=rng.choice(names),
                cpf=rng.choice(docs),
                signed=rng.choice(["2026-03-10", "2024-12-31", "2026-08-01"]),
            )
            for i in range(30)
        ]
        return contracts, sanctions

    def test_matches_the_naive_reference_on_random_corpora(self) -> None:
        for seed in range(20):
            contracts, sanctions = self._corpus(seed)
            assert sanctioned_name_match_signals(contracts, sanctions) == (
                _naive_reference(contracts, sanctions)
            ), f"divergence on seed {seed}"
