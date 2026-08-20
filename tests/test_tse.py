"""Tests for the TSE campaign finance dump crawler and parser (O8).

Responsibility: Validate the CampaignDonation model, the streaming ZIP
parser (receitas_candidatos layout: header row, latin1, semicolon, comma
decimal) and the snapshot downloader (year-derived file name, skip/on_file
resume, CDN error pages rejected) with minimal local fixtures — the real
CDN is geo-restricted (PR-D-08 §2).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from capiba.ingestion.crawler_tse import download_tse_dump, tse_dump_filename
from capiba.ingestion.tse import (
    CampaignDonation,
    donation_id,
    entity_for_dump,
    parse_tse_zip,
)

HEADER = (
    "DT_PRESTACAO_CONTAS;SQ_PRESTADOR_CONTAS;NR_CNPJ_CAMPANHA;SQ_CANDIDATO;"
    "NM_CANDIDATO;SG_PARTIDO;DS_CARGO;NM_UE;SG_UF;DS_ORIGEM_RECEITA;SQ_RECEITA;"
    "DT_RECEITA;VR_RECEITA;NR_CPF_CNPJ_DOADOR;NM_DOADOR;NM_DOADOR_RFB;"
    "NR_CPF_CNPJ_DOADOR_ORIGINARIO;NM_DOADOR_ORIGINARIO;NM_DOADOR_ORIGINARIO_RFB"
)

PJ_ROW = (
    "2024-11-05;111;42000191;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de pessoas jurídicas;555;2024-08-15T00:00:00;50.000,00;"
    "12345678000190;EMPRESA DOADORA;EMPRESA DOADORA LTDA;;;"
)
PF_ROW = (
    "2024-11-05;111;42000191;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de pessoas físicas;556;15/09/2024;1.000,50;123.456.789-01;"
    "JOAO D***;JOAO DA SILVA;;;"
)
VIA_PARTY_ROW = (
    "2024-11-05;222;42000191;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de partido político;557;2024-10-01;20.000,00;99000191000155;"
    "DIRETORIO MUNICIPAL XX;DIRETORIO MUNICIPAL XX;87654321000143;"
    "CONSTRUTORA ORIG;CONSTRUTORA ORIGEM LTDA"
)
BAD_DATE_ROW = (
    "2024-11-05;111;42000191;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de pessoas físicas;558;31/99/2024;10,00;12345678901;X;X;;;"
)
SPECIAL_DOC_ROW = (
    "2024-11-05;111;42000191;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos próprios;559;2024-09-20;300,00;-2;NAO IDENTIFICADO;;-4;;"
)


def _write_zip(path: Path, members: dict[str, list[str]]) -> Path:
    """Writes a fixture TSE dump ZIP (latin1, header row per member)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for member, rows in members.items():
            zf.writestr(member, "\n".join([HEADER, *rows]).encode("latin1"))
    path.write_bytes(buffer.getvalue())
    return path


def _receitas_zip(path: Path, rows: list[str], scope: str = "BRASIL") -> Path:
    return _write_zip(path, {f"receitas_candidatos_2024_{scope}.csv": rows})


class TestEntityForDump:
    """Tests for the TSE dump ZIP name -> entity mapping."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("prestacao_de_contas_eleitorais_candidatos_2024.zip", "campaign_donations"),
            ("prestacao_de_contas_eleitorais_candidatos_2022.zip", "campaign_donations"),
            ("prestacao_de_contas_eleitorais_orgaos_partidarios_2024.zip", None),
            ("consulta_cand_2024.zip", None),
            ("Empresas0.zip", None),
        ],
    )
    def test_mapping(self, filename: str, expected: str | None) -> None:
        assert entity_for_dump(filename) == expected


class TestCampaignDonationModel:
    """Tests for the CampaignDonation pydantic model."""

    def _parse_row(self, row: str) -> CampaignDonation:
        columns = HEADER.split(";")
        values = row.split(";")
        assert len(columns) == len(values)
        return CampaignDonation.model_validate(
            {**dict(zip(columns, values, strict=True)), "election_year": 2024}
        )

    def test_pj_row(self) -> None:
        donation = self._parse_row(PJ_ROW)

        assert donation.id == donation_id("111", "555")
        assert donation.election_year == 2024
        assert donation.donor_document == "12345678000190"
        assert donation.donor_name == "EMPRESA DOADORA LTDA"  # RFB name wins
        assert donation.donation_date == date(2024, 8, 15)  # ISO datetime
        assert donation.amount == Decimal("50000.00")  # comma decimal
        assert donation.revenue_origin == "Recursos de pessoas jurídicas"
        assert donation.candidate_sequential == "9001"
        assert donation.party == "XX"
        assert donation.office == "Prefeito"
        assert donation.ue_name == "RECIFE"
        assert donation.uf == "PE"

    def test_pf_row_brazilian_date_and_masked_name(self) -> None:
        """DD/MM/YYYY dates parse; punctuation is stripped from the CPF."""
        donation = self._parse_row(PF_ROW)

        assert donation.donor_document == "12345678901"
        assert donation.donor_name == "JOAO DA SILVA"  # NM_DOADOR masked, RFB full
        assert donation.donation_date == date(2024, 9, 15)
        assert donation.amount == Decimal("1000.50")

    def test_origin_donor(self) -> None:
        """Donations via a party directory keep the origin donor document."""
        donation = self._parse_row(VIA_PARTY_ROW)

        assert donation.donor_document == "99000191000155"  # the directory
        assert donation.donor_origin_document == "87654321000143"
        assert donation.donor_origin_name == "CONSTRUTORA ORIGEM LTDA"

    def test_special_document_codes_become_none(self) -> None:
        """Non CPF/CNPJ placeholders ("-2"/"-4") are kept as NULL documents."""
        donation = self._parse_row(SPECIAL_DOC_ROW)

        assert donation.donor_document is None
        assert donation.donor_origin_document is None
        assert donation.donor_name == "NAO IDENTIFICADO"  # NM_DOADOR fallback
        assert donation.donation_date == date(2024, 9, 20)

    def test_invalid_date_fails(self) -> None:
        with pytest.raises(ValueError):
            self._parse_row(BAD_DATE_ROW)

    def test_silver_rows_revalidate(self) -> None:
        """JSON-mode dumps (silver rows) round-trip through the model."""
        donation = self._parse_row(PJ_ROW)
        dumped = donation.model_dump(mode="json")
        assert (
            CampaignDonation.model_validate(dumped).model_dump(mode="json") == dumped
        )


class TestParseTseZip:
    """Tests for the streaming TSE ZIP parser."""

    def test_parse_receitas_chunked(self, tmp_path: Path) -> None:
        """Rows are validated per chunk; invalid rows are skipped."""
        zip_path = _receitas_zip(
            tmp_path / "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            [PJ_ROW, BAD_DATE_ROW, PF_ROW],
        )

        chunks = list(parse_tse_zip(zip_path, chunk_size=1))

        assert len(chunks) == 3
        entity, records, errors = chunks[0]
        assert entity == "campaign_donations"
        assert records[0]["donor_document"] == "12345678000190"
        assert records[0]["amount"] == "50000.00"
        assert records[0]["donation_date"] == "2024-08-15"
        assert records[0]["election_year"] == 2024
        assert chunks[1][1] == [] and chunks[1][2] == 1  # invalid row counted
        assert chunks[2][2] == 0

    def test_brasil_member_wins_over_per_uf(self, tmp_path: Path) -> None:
        """The consolidated _BRASIL member is parsed; per-UF ones are skipped."""
        zip_path = _write_zip(
            tmp_path / "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            {
                "receitas_candidatos_2024_BRASIL.csv": [PJ_ROW],
                "receitas_candidatos_2024_PE.csv": [PF_ROW],
                "receitas_candidatos_2024_SP.csv": [PF_ROW],
            },
        )

        chunks = list(parse_tse_zip(zip_path))

        documents = [r["donor_document"] for _, records, _ in chunks for r in records]
        assert documents == ["12345678000190"]

    def test_per_uf_fallback_without_brasil(self, tmp_path: Path) -> None:
        """ZIPs without the consolidated member parse the per-UF files."""
        zip_path = _write_zip(
            tmp_path / "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            {
                "receitas_candidatos_2024_PE.csv": [PJ_ROW],
                "receitas_candidatos_2024_SP.csv": [PF_ROW],
            },
        )

        chunks = list(parse_tse_zip(zip_path))

        documents = [r["donor_document"] for _, records, _ in chunks for r in records]
        assert documents == ["12345678000190", "12345678901"]

    def test_despesas_members_are_skipped(self, tmp_path: Path) -> None:
        """Expense members are not part of the v1 entity (PR-D-08 §2)."""
        zip_path = _write_zip(
            tmp_path / "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            {
                "receitas_candidatos_2024_BRASIL.csv": [PJ_ROW],
                "despesas_contratadas_candidatos_2024_BRASIL.csv": [PF_ROW],
            },
        )

        chunks = list(parse_tse_zip(zip_path))

        assert len(chunks) == 1
        assert chunks[0][1][0]["donor_document"] == "12345678000190"

    def test_non_entity_zip_is_rejected(self, tmp_path: Path) -> None:
        """Non-entity dumps (party accounts etc.) are not parseable."""
        zip_path = _write_zip(
            tmp_path / "prestacao_de_contas_eleitorais_orgaos_partidarios_2024.zip",
            {"receitas_orgaos_partidarios_2024_BRASIL.csv": [PJ_ROW]},
        )

        assert entity_for_dump(zip_path.name) is None
        with pytest.raises(ValueError, match="Unrecognized TSE dump file"):
            list(parse_tse_zip(zip_path))


class _FakeResponse:
    """Minimal requests.Response stand-in for streaming downloads."""

    def __init__(self, body: bytes, status_ok: bool = True) -> None:
        self._body = body
        self._status_ok = status_ok

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if not self._status_ok:
            import requests

            raise requests.HTTPError("403 Forbidden")

    def iter_content(self, chunk_size: int = 8192) -> Any:
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset : offset + chunk_size]


class TestDownloadTseDump:
    """Tests for the snapshot downloader contract."""

    def _zip_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("receitas_candidatos_2024_BRASIL.csv", "header")
        return buffer.getvalue()

    def test_downloads_year_derived_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default file list derives from the election year."""
        seen_urls: list[str] = []
        body = self._zip_bytes()

        def fake_get(url: str, **_kwargs: Any) -> _FakeResponse:
            seen_urls.append(url)
            return _FakeResponse(body)

        monkeypatch.setattr("capiba.ingestion.crawler_tse.requests.get", fake_get)

        downloaded = download_tse_dump(tmp_path, "2026-07", year=2024)

        assert [p.name for p in downloaded] == [
            "prestacao_de_contas_eleitorais_candidatos_2024.zip"
        ]
        assert seen_urls == [
            "https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/"
            "prestacao_de_contas_eleitorais_candidatos_2024.zip"
        ]

    def test_reference_month_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fixed snapshot does not depend on the reference month."""
        body = self._zip_bytes()
        monkeypatch.setattr(
            "capiba.ingestion.crawler_tse.requests.get",
            lambda *_a, **_k: _FakeResponse(body),
        )

        first = download_tse_dump(tmp_path / "a", "2026-01")
        second = download_tse_dump(tmp_path / "b", "2026-12")

        assert [p.name for p in first] == [p.name for p in second]

    def test_skip_and_on_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Registry resume contract: skip is honored, on_file fires per file."""
        body = self._zip_bytes()
        monkeypatch.setattr(
            "capiba.ingestion.crawler_tse.requests.get",
            lambda *_a, **_k: _FakeResponse(body),
        )
        uploaded: list[str] = []

        downloaded = download_tse_dump(
            tmp_path,
            "2026-07",
            files=["a_2024.zip", "b_2024.zip"],
            skip={"a_2024.zip"},
            on_file=lambda path: uploaded.append(path.name),
        )

        assert [p.name for p in downloaded] == ["b_2024.zip"]
        assert uploaded == ["b_2024.zip"]

    def test_cdn_error_page_is_not_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An HTML payload (Akamai 403/200 error page) is discarded."""
        monkeypatch.setattr(
            "capiba.ingestion.crawler_tse.requests.get",
            lambda *_a, **_k: _FakeResponse(b"<HTML><TITLE>Access Denied</TITLE>"),
        )

        assert download_tse_dump(tmp_path, "2026-07") == []
        assert list(tmp_path.iterdir()) == []

    def test_http_error_is_logged_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed download (geo-restricted CDN) yields an empty list."""
        monkeypatch.setattr(
            "capiba.ingestion.crawler_tse.requests.get",
            lambda *_a, **_k: _FakeResponse(b"", status_ok=False),
        )

        assert download_tse_dump(tmp_path, "2026-07") == []


class TestRegistry:
    """The tse source and parser are registered for the declarative specs."""

    def test_source_registered(self) -> None:
        from capiba.pipeline.registry import SOURCE_REGISTRY

        assert SOURCE_REGISTRY["tse"].download is download_tse_dump

    def test_dump_parser_registered(self) -> None:
        from capiba.pipeline.registry import DUMP_PARSER_REGISTRY

        dump = DUMP_PARSER_REGISTRY["tse"]
        assert dump.parse is parse_tse_zip
        assert dump.entity_for_file is entity_for_dump

    def test_federal_revenue_parser_keeps_mapping(self) -> None:
        """The CNPJ parser migrated to DumpParserDef without behavior change."""
        from capiba.pipeline.registry import DUMP_PARSER_REGISTRY

        dump = DUMP_PARSER_REGISTRY["federal_revenue"]
        assert dump.entity_for_file("Empresas0.zip") == "companies"
        assert dump.entity_for_file("Cnaes.zip") is None

    def test_tse_dump_filename(self) -> None:
        assert (
            tse_dump_filename(2024)
            == "prestacao_de_contas_eleitorais_candidatos_2024.zip"
        )
