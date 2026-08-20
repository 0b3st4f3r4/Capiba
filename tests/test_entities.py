"""Tests for the entity resolution matchers (O5 / PR-D-07).

Responsibility: Validate the pre-registered semantics — name
normalization, masked-document matching, the conservative scoring
(name alone never merges) and the deterministic supplier↔company link —
with no external infrastructure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from capiba.detection.entities import (
    documents_match,
    is_merge,
    link_supplier_company,
    name_similarity,
    normalize_name,
    resolve_entities,
    score_person_pair,
)


class TestNormalizeName:
    """Tests for normalize_name."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("João Silva", "JOAO SILVA"),
            ("MARIA  de   SOUZA", "DE MARIA SOUZA"),  # tokens sorted
            ("Empresa S.A.", "A EMPRESA S"),  # punctuation out
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalization(self, raw: str | None, expected: str) -> None:
        assert normalize_name(raw) == expected


class TestNameSimilarity:
    """Tests for name_similarity."""

    def test_identical_names_score_one(self) -> None:
        assert name_similarity("João Silva", "JOAO SILVA") == 1.0

    def test_token_order_is_irrelevant(self) -> None:
        assert name_similarity("SILVA JOAO", "João Silva") == 1.0

    def test_disjoint_names_score_low(self) -> None:
        assert name_similarity("JOAO SILVA", "MARIA SOUZA") < 0.5

    def test_missing_name_scores_zero(self) -> None:
        assert name_similarity(None, "JOAO") == 0.0


class TestDocumentsMatch:
    """Tests for documents_match (masked-document discipline)."""

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("***123456**", "***123456**", True),
            ("***123456**", "12312345601", True),  # masked vs full
            ("12345678000195", "12345678000195", True),
            ("***123456**", "***654321**", False),
            ("", "12345678000195", False),
            (None, None, False),
        ],
    )
    def test_matching(self, a: str | None, b: str | None, expected: bool) -> None:
        assert documents_match(a, b) is expected


class TestScorePersonPair:
    """Tests for the pre-registered scoring (weights 0.6/0.3/0.1)."""

    def test_full_match_scores_one(self) -> None:
        a = {"nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**", "faixa_etaria": "5"}
        b = dict(a)
        assert score_person_pair(a, b) == 1.0

    def test_name_alone_caps_below_threshold(self) -> None:
        """E5: identical names without a document never merge."""
        a = {"nome": "JOAO SILVA"}
        b = {"nome": "JOAO SILVA"}
        assert score_person_pair(a, b) == pytest.approx(0.6)
        assert not is_merge(score_person_pair(a, b))

    def test_noisy_name_with_same_document_merges(self) -> None:
        """E2: accent/case/order noise does not block the merge."""
        a = {"nome": "João da Silva", "cnpj_cpf_socio": "***123456**"}
        b = {"nome": "SILVA, JOAO DA", "cnpj_cpf_socio": "***123456**"}
        assert is_merge(score_person_pair(a, b))

    def test_homonyms_do_not_merge(self) -> None:
        """E3: same name, different documents — not the same person."""
        a = {"nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**", "faixa_etaria": "5"}
        b = {"nome": "JOAO SILVA", "cnpj_cpf_socio": "***654321**", "faixa_etaria": "5"}
        assert not is_merge(score_person_pair(a, b))

    def test_disjoint_names_with_same_document_do_not_merge(self) -> None:
        """E4: masked-digit coincidence does not merge disjoint names."""
        a = {"nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**"}
        b = {"nome": "MARIA SOUZA", "cnpj_cpf_socio": "***123456**"}
        assert not is_merge(score_person_pair(a, b))

    def test_age_range_is_evidence_not_veto(self) -> None:
        """E6: diverging age range still merges with name + document."""
        a = {"nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**", "faixa_etaria": "5"}
        b = {"nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**", "faixa_etaria": "6"}
        assert score_person_pair(a, b) == pytest.approx(0.9)
        assert is_merge(score_person_pair(a, b))

    def test_opensanctions_aliases(self) -> None:
        """The OS Pairs regime maps to name/id_number aliases."""
        a = {"name": "John Smith", "id_number": "A123"}
        b = {"name": "John Smith", "id_number": "A123"}
        assert score_person_pair(a, b) == pytest.approx(0.9)


class TestLinkSupplierCompany:
    """Tests for the deterministic supplier↔company link."""

    def test_full_cnpj_links_by_cnpj_basico(self) -> None:
        assert link_supplier_company("12345678000195", "12345678") == 1.0

    def test_mismatched_cnpj_does_not_link(self) -> None:
        assert link_supplier_company("12345678000195", "87654321") == 0.0

    def test_supplier_without_cnpj_never_links(self) -> None:
        assert link_supplier_company(None, "12345678") == 0.0
        assert link_supplier_company("", "12345678") == 0.0


class TestResolveEntities:
    """Tests for resolve_entities (same_as edges above the threshold)."""

    def _patch(self, monkeypatch, rows: list[dict]) -> tuple[MagicMock, MagicMock]:
        """Patches the graph I/O of the module; returns (aql, upsert)."""
        execute = MagicMock(return_value=rows)
        upsert = MagicMock()
        monkeypatch.setattr("capiba.db.arangodb.execute_aql", execute)
        monkeypatch.setattr("capiba.db.arangodb.upsert_edge", upsert)
        return execute, upsert

    def test_same_person_across_companies_merges(self, monkeypatch) -> None:
        """Two vertices with same name + masked document yield an edge."""
        rows = [
            {"_key": "p1", "nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**"},
            {"_key": "p2", "nome": "João Silva", "cnpj_cpf_socio": "***123456**"},
        ]
        _, upsert = self._patch(monkeypatch, rows)

        summary = resolve_entities(db=MagicMock())

        assert summary == {
            "persons": 2,
            "candidate_pairs": 1,
            "same_as": 1,
            "threshold": 0.85,
        }
        args = upsert.call_args.args
        assert args[1] == "same_as"
        assert {args[2], args[3]} == {"persons/p1", "persons/p2"}
        payload = upsert.call_args.args[4]
        assert payload["score"] >= 0.85
        assert "source_rows" in payload["details"]

    def test_homonyms_never_merge(self, monkeypatch) -> None:
        """Same name with different masked documents stays two vertices."""
        from unittest.mock import MagicMock

        rows = [
            {"_key": "p1", "nome": "JOAO SILVA", "cnpj_cpf_socio": "***123456**"},
            {"_key": "p2", "nome": "JOAO SILVA", "cnpj_cpf_socio": "***654321**"},
        ]
        _, upsert = self._patch(monkeypatch, rows)

        summary = resolve_entities(db=MagicMock())

        assert summary["same_as"] == 0
        upsert.assert_not_called()

    def test_name_alone_does_not_merge(self, monkeypatch) -> None:
        """Without a document the name alone caps below the threshold (E5)."""
        from unittest.mock import MagicMock

        rows = [
            {"_key": "p1", "nome": "JOAO SILVA"},
            {"_key": "p2", "nome": "JOAO SILVA"},
        ]
        _, upsert = self._patch(monkeypatch, rows)

        summary = resolve_entities(db=MagicMock())

        assert summary["candidate_pairs"] == 1  # same name-token block
        assert summary["same_as"] == 0
        upsert.assert_not_called()
