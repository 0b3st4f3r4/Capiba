"""Tests for the Federal Revenue CNPJ dump parser.

Responsibility: Validate the entity models and the streaming ZIP parser
with minimal fixture dumps (Empresas/Estabelecimentos/Socios layouts,
including comma decimals, "00000000" dates and invalid rows), with no
external infrastructure.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from capiba.ingestion.cnpj import (
    Company,
    Establishment,
    Partner,
    edge_kind_for_qualificacao,
    entity_for_zip,
    parse_cnpj_zip,
    partner_key,
)


def _write_zip(path: Path, member: str, rows: list[str]) -> Path:
    """Writes a fixture ZIP with one member holding the given CSV rows."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(member, "\n".join(rows).encode("latin1"))
    path.write_bytes(buffer.getvalue())
    return path


def _companies_zip(path: Path, rows: list[str]) -> Path:
    return _write_zip(path, "K3241.K03200Y0.D50610.EMPRECSV", rows)


COMPANY_ROW = "12345678;EMPRESA TESTE LTDA;2062;49;1.000,50;05;PE"
ESTABLISHMENT_ROW = (
    "12345678;0001;95;1;FILIAL TESTE;02;20200101;00;;"
    "7107;20150601;6201501;;RUA;DAS FLORES;100;SALA 2;CENTRO;"
    "50000000;PE;7107;81;999999999;;;;;contato@teste.com;;20200102"
)
PARTNER_ROW = "12345678;2;JOAO SILVA;***123456**;22;20150101;;;;;5"


class TestEntityForZip:
    """Tests for the ZIP name -> entity mapping."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("Empresas0.zip", "companies"),
            ("Estabelecimentos9.zip", "establishments"),
            ("Socios3.zip", "partners"),
            ("Cnaes.zip", None),
            ("Motivos.zip", None),
            ("Simples.zip", None),
        ],
    )
    def test_mapping(self, filename: str, expected: str | None) -> None:
        assert entity_for_zip(filename) == expected


class TestModels:
    """Tests for the pydantic entity models."""

    def test_company_comma_decimal(self) -> None:
        """capital_social uses the Brazilian comma decimal separator."""
        company = Company.model_validate(
            {"cnpj_basico": "12345678", "capital_social": "1.234,56"}
        )
        assert company.capital_social == Decimal("1234.56")

    def test_company_invalid_cnpj_basico(self) -> None:
        with pytest.raises(ValueError):
            Company.model_validate({"cnpj_basico": "123"})

    def test_establishment_composes_full_cnpj(self) -> None:
        establishment = Establishment.model_validate(
            {
                "cnpj_basico": "12345678",
                "cnpj_ordem": "0001",
                "cnpj_dv": "95",
                "identificador_matriz_filial": "1",
                "data_situacao_cadastral": "20200101",
            }
        )
        assert establishment.cnpj == "12345678000195"
        assert establishment.is_matriz is True
        assert establishment.data_situacao_cadastral == date(2020, 1, 1)

    def test_establishment_zero_date_is_none(self) -> None:
        establishment = Establishment.model_validate(
            {
                "cnpj_basico": "12345678",
                "cnpj_ordem": "0002",
                "cnpj_dv": "76",
                "identificador_matriz_filial": "2",
                "data_situacao_cadastral": "00000000",
            }
        )
        assert establishment.is_matriz is False
        assert establishment.data_situacao_cadastral is None

    def test_partner_id_ignores_masked_document(self) -> None:
        """The partner key derives from company+name+qualification."""
        partner = Partner.model_validate(
            {
                "cnpj_basico": "12345678",
                "identificador_socio": "2",
                "nome_socio_razao_social": "JOAO SILVA",
                "cnpj_cpf_socio": "***123456**",
                "qualificacao_socio": "22",
            }
        )
        assert partner.partner_id == partner_key("12345678", "JOAO SILVA", "22")
        assert len(partner.partner_id) == 32
        assert partner.cnpj_cpf_socio == "***123456**"

    def test_partner_keeps_representante_legal(self) -> None:
        """The legal representative columns survive into the silver row."""
        partner = Partner.model_validate(
            {
                "cnpj_basico": "12345678",
                "identificador_socio": "2",
                "nome_socio_razao_social": "MARIA SOUZA",
                "cnpj_cpf_socio": "***987654**",
                "qualificacao_socio": "22",
                "pais": "076",
                "representante_legal": "00000000000",
                "nome_representante": "JOAO SILVA",
                "qualificacao_representante_legal": "05",
            }
        )
        assert partner.pais == "076"
        assert partner.representante_legal == "00000000000"
        assert partner.nome_representante == "JOAO SILVA"
        assert partner.qualificacao_representante_legal == "05"


class TestEdgeKindForQualificacao:
    """Tests for the RFB qualification -> FtM edge classification."""

    @pytest.mark.parametrize(
        ("qualificacao", "expected"),
        [
            ("22", "ownership"),  # Sócio
            ("48", "ownership"),  # Sócio PJ Domiciliado no Brasil
            (None, "ownership"),  # missing defaults to equity
            ("05", "directorship"),  # Administrador
            ("10", "directorship"),  # Diretor
            ("16", "directorship"),  # Presidente
            ("49", "both"),  # Sócio-Administrador
            ("28", "both"),  # Sócio-Gerente
        ],
    )
    def test_classification(self, qualificacao: str | None, expected: str) -> None:
        assert edge_kind_for_qualificacao(qualificacao) == expected

    def test_models_revalidate_silver_rows(self) -> None:
        """JSON-mode dumps (silver rows) round-trip through the models."""
        for model, row in (
            (Company, {"cnpj_basico": "12345678", "capital_social": "10.00"}),
            (
                Establishment,
                {
                    "cnpj": "12345678000195",
                    "cnpj_basico": "12345678",
                    "is_matriz": True,
                    "data_inicio_atividade": "2015-06-01",
                },
            ),
            (
                Partner,
                {
                    "partner_id": partner_key("12345678", "JOAO", "22"),
                    "cnpj_basico": "12345678",
                    "data_entrada": None,
                },
            ),
        ):
            dumped = model.model_validate(row).model_dump(mode="json")
            assert model.model_validate(dumped).model_dump(mode="json") == dumped


class TestParseCnpjZip:
    """Tests for the streaming ZIP parser."""

    def test_parse_companies_chunked(self, tmp_path: Path) -> None:
        """Rows are validated per chunk; invalid rows are skipped."""
        zip_path = _companies_zip(
            tmp_path / "Empresas0.zip",
            [COMPANY_ROW, "999;INVALIDA;2062;49;10,00;05;", COMPANY_ROW],
        )

        chunks = list(parse_cnpj_zip(zip_path, chunk_size=1))

        assert len(chunks) == 3
        entity, records, errors = chunks[0]
        assert entity == "companies"
        assert records[0]["cnpj_basico"] == "12345678"
        assert records[0]["capital_social"] == "1000.50"
        assert records[0]["ente_federativo"] == "PE"
        assert chunks[1][1] == [] and chunks[1][2] == 1  # invalid row counted
        assert chunks[2][2] == 0

    def test_parse_establishments_member_extension(self, tmp_path: Path) -> None:
        """Members with the .ESTABELE extension are read directly."""
        zip_path = _write_zip(
            tmp_path / "Estabelecimentos0.zip",
            "K3241.K03200Y0.D50610.ESTABELE",
            [ESTABLISHMENT_ROW],
        )

        entity, records, errors = next(iter(parse_cnpj_zip(zip_path)))

        assert (entity, errors) == ("establishments", 0)
        assert records[0]["cnpj"] == "12345678000195"
        assert records[0]["is_matriz"] is True
        assert records[0]["data_situacao_cadastral"] == "2020-01-01"
        assert records[0]["uf"] == "PE"
        assert records[0]["cep"] == "50000000"
        assert records[0]["cnae_principal"] == "6201501"
        assert records[0]["email"] == "contato@teste.com"

    def test_parse_partners(self, tmp_path: Path) -> None:
        zip_path = _write_zip(
            tmp_path / "Socios0.zip",
            "K3241.K03200Y0.D50610.SOCIOCSV",
            [PARTNER_ROW],
        )

        entity, records, errors = next(iter(parse_cnpj_zip(zip_path)))

        assert (entity, errors) == ("partners", 0)
        assert records[0]["partner_id"] == partner_key("12345678", "JOAO SILVA", "22")
        assert records[0]["nome"] == "JOAO SILVA"
        assert records[0]["data_entrada"] == "2015-01-01"
        assert records[0]["faixa_etaria"] == "5"
        assert records[0]["cnpj_cpf_socio"] == "***123456**"

    def test_reference_zip_is_rejected(self, tmp_path: Path) -> None:
        """Non-entity files (Cnaes.zip etc.) are not parseable."""
        zip_path = _write_zip(tmp_path / "Cnaes.zip", "cnaes.csv", ["6201501;TI"])

        assert entity_for_zip(zip_path.name) is None
        with pytest.raises(ValueError, match="Unrecognized CNPJ dump file"):
            list(parse_cnpj_zip(zip_path))
