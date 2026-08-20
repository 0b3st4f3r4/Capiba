"""Tests for the FollowTheMoney JSON export (O4).

Responsibility: Validate the FtM serialization of the company subgraph
(Company/Person vertices, Ownership/Directorship edges) with a mocked
ArangoDB, with no external infrastructure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from capiba.db import ftm


def _fake_db() -> MagicMock:
    return MagicMock()


def _subgraph() -> dict:
    """A company with a person partner (ownership) and a holding."""
    return {
        "company": {
            "_id": "companies/12345678",
            "_key": "12345678",
            "schema": "Company",
            "cnpj_basico": "12345678",
            "razao_social": "ACME LTDA",
        },
        "inbound": [
            {
                "vertex": {
                    "_id": "persons/p1",
                    "_key": "p1",
                    "schema": "Person",
                    "nome": "JOAO SILVA",
                    "cnpj_cpf_socio": "***123456**",
                },
                "edge": {
                    "_id": "ownership/persons_p1__companies_12345678",
                    "_key": "persons_p1__companies_12345678",
                    "_from": "persons/p1",
                    "_to": "companies/12345678",
                    "schema": "Ownership",
                    "qualificacao": "22",
                    "data_entrada": "2015-01-01",
                },
            },
            {
                "vertex": {
                    "_id": "persons/p2",
                    "_key": "p2",
                    "schema": "Person",
                    "nome": "MARIA SOUZA",
                },
                "edge": {
                    "_id": "directorship/persons_p2__companies_12345678",
                    "_key": "persons_p2__companies_12345678",
                    "_from": "persons/p2",
                    "_to": "companies/12345678",
                    "schema": "Directorship",
                    "qualificacao": "05",
                },
            },
        ],
        "outbound": [
            {
                "vertex": {
                    "_id": "companies/99888777",
                    "_key": "99888777",
                    "schema": "Company",
                    "cnpj_basico": "99888777",
                    "razao_social": "SUBSIDIARIA SA",
                },
                "edge": {
                    "_id": "ownership/companies_12345678__companies_99888777",
                    "_key": "companies_12345678__companies_99888777",
                    "_from": "companies/12345678",
                    "_to": "companies/99888777",
                    "schema": "Ownership",
                    "qualificacao": "48",
                },
            }
        ],
    }


class TestExportFtmEntities:
    """Tests for export_ftm_entities."""

    def _mock_aql(self, monkeypatch, rows: list) -> MagicMock:
        execute = MagicMock(return_value=rows)
        monkeypatch.setattr("capiba.db.ftm.execute_aql", execute)
        return execute

    def test_full_subgraph_export(self, monkeypatch) -> None:
        """Company, partners and holdings export as FtM entities."""
        execute = self._mock_aql(monkeypatch, [_subgraph()])

        entities = ftm.export_ftm_entities("12345678000195", db=_fake_db())

        assert execute.call_args.args[2] == {"cnpj": "12345678"}
        by_id = {e["id"]: e for e in entities}
        assert by_id["company-12345678"] == {
            "id": "company-12345678",
            "schema": "Company",
            "properties": {
                "name": ["ACME LTDA"],
                "registrationNumber": ["12345678"],
            },
        }
        assert by_id["person-p1"]["schema"] == "Person"
        assert by_id["person-p1"]["properties"]["idNumber"] == ["***123456**"]
        ownership = by_id["ownership-persons_p1__companies_12345678"]
        assert ownership["schema"] == "Ownership"
        assert ownership["properties"]["owner"] == ["person-p1"]
        assert ownership["properties"]["asset"] == ["company-12345678"]
        assert ownership["properties"]["startDate"] == ["2015-01-01"]
        directorship = by_id["directorship-persons_p2__companies_12345678"]
        assert directorship["schema"] == "Directorship"
        assert directorship["properties"]["director"] == ["person-p2"]
        assert directorship["properties"]["organization"] == ["company-12345678"]
        holding = by_id["ownership-companies_12345678__companies_99888777"]
        assert holding["properties"]["owner"] == ["company-12345678"]
        assert holding["properties"]["asset"] == ["company-99888777"]
        assert len(entities) == 7  # 3 vertices + 1 holding vertex + 3 edges

    def test_unknown_company_returns_empty(self, monkeypatch) -> None:
        """A CNPJ absent from the graph exports nothing."""
        self._mock_aql(monkeypatch, [{"company": None, "inbound": [], "outbound": []}])

        assert ftm.export_ftm_entities("00000000", db=_fake_db()) == []

    def test_empty_result_returns_empty(self, monkeypatch) -> None:
        self._mock_aql(monkeypatch, [])

        assert ftm.export_ftm_entities("00000000", db=_fake_db()) == []

    def test_creates_default_connection(self, monkeypatch) -> None:
        """When db is None, get_capiba_db must provide the connection."""
        db = _fake_db()
        get_db = MagicMock(return_value=db)
        monkeypatch.setattr("capiba.db.ftm.get_capiba_db", get_db)
        monkeypatch.setattr("capiba.db.ftm.execute_aql", MagicMock(return_value=[]))

        assert ftm.export_ftm_entities("12345678") == []
        get_db.assert_called_once_with()
