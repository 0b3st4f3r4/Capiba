"""Tests for the TSE campaign finance dump crawler and parser (O8).

Responsibility: Validate the CampaignDonation model, the streaming ZIP
parser (receitas_candidatos layout: header row, latin1, semicolon, comma
decimal) and the snapshot resolver (year-derived file names from the
frozen bronze anchor ``tse/reference/``, skip/on_file resume, missing or
corrupt anchor fails loudly) with minimal local fixtures — the lake is
mocked, the TSE CDN is unreachable from CLI clients (Akamai 403).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from capiba.ingestion.crawler_tse import (
    download_tse_dump,
    tse_candidates_filename,
    tse_dump_filename,
)
from capiba.ingestion.tse import (
    CampaignDonation,
    Candidacy,
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


CAND_HEADER = (
    "SQ_CANDIDATO;NM_CANDIDATO;SG_PARTIDO;DS_CARGO;CD_UE;NM_UE;SG_UF;"
    "DS_SITUACAO_TOTALIZACAO_TURNO"
)
# Post-2026 republished layout: DS_SIT_TOT_TURNO and SG_UE (no CD_UE).
CAND_HEADER_2026 = (
    "SQ_CANDIDATO;NM_CANDIDATO;SG_PARTIDO;DS_CARGO;SG_UE;NM_UE;SG_UF;"
    "DS_SIT_TOT_TURNO"
)
ELECTED_MAYOR_ROW = "9001;JOANA CANDIDATA;XX;Prefeito;25313;RECIFE;PE;Eleito"
DEFEATED_MAYOR_ROW = "9002;ZE DERROTADO;YY;Prefeito;25313;RECIFE;PE;Não eleito"
ELECTED_COUNCILLOR_ROW = (
    "9003;MARIA VEREADORA;XX;Vereador;25313;RECIFE;PE;Eleito por QP"
)


def _write_zip(
    path: Path, members: dict[str, list[str]], header: str = HEADER
) -> Path:
    """Writes a fixture TSE dump ZIP (latin1, header row per member)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for member, rows in members.items():
            zf.writestr(member, "\n".join([header, *rows]).encode("latin1"))
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
            ("consulta_cand_2024.zip", "candidacies"),
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


class TestCandidacyModel:
    """Tests for the Candidacy pydantic model (consulta_cand dump)."""

    def _parse_row(self, row: str) -> Candidacy:
        columns = CAND_HEADER.split(";")
        values = row.split(";")
        assert len(columns) == len(values)
        return Candidacy.model_validate(
            {**dict(zip(columns, values, strict=True)), "election_year": 2024}
        )

    def test_elected_mayor_row(self) -> None:
        candidacy = self._parse_row(ELECTED_MAYOR_ROW)

        assert candidacy.candidate_sequential == "9001"
        assert candidacy.candidate_name == "JOANA CANDIDATA"
        assert candidacy.party == "XX"
        assert candidacy.office == "Prefeito"
        assert candidacy.ue_code == "25313"
        assert candidacy.ue_name == "RECIFE"
        assert candidacy.uf == "PE"
        assert candidacy.totalization_status == "Eleito"
        assert len(candidacy.id) == 32

    def test_id_is_stable_per_year_and_sequential(self) -> None:
        first = self._parse_row(ELECTED_MAYOR_ROW)
        second = self._parse_row(ELECTED_MAYOR_ROW)
        other_sequential = self._parse_row(DEFEATED_MAYOR_ROW)

        assert first.id == second.id
        assert other_sequential.id != first.id

    def test_silver_shape_row_passes_through(self) -> None:
        row = self._parse_row(DEFEATED_MAYOR_ROW).model_dump()

        assert Candidacy.model_validate(row) == Candidacy(**row)

    def test_post_2026_layout_row(self) -> None:
        """The republished dumps (DS_SIT_TOT_TURNO / SG_UE) parse the same."""
        columns = CAND_HEADER_2026.split(";")
        row = dict(zip(columns, ELECTED_MAYOR_ROW.split(";"), strict=True))
        candidacy = Candidacy.model_validate({**row, "election_year": 2024})

        assert candidacy.ue_code == "25313"
        assert candidacy.totalization_status == "Eleito"
        assert candidacy.id == self._parse_row(ELECTED_MAYOR_ROW).id


class TestParseCandidaciesZip:
    """Tests for the consulta_cand ZIP parsing."""

    def _cand_zip(self, path: Path, members: dict[str, list[str]]) -> Path:
        return _write_zip(path, members, header=CAND_HEADER)

    def test_parses_brasil_member(self, tmp_path: Path) -> None:
        zip_path = self._cand_zip(
            tmp_path / "consulta_cand_2024.zip",
            {"consulta_cand_2024_BRASIL.csv": [ELECTED_MAYOR_ROW, DEFEATED_MAYOR_ROW]},
        )

        chunks = list(parse_tse_zip(zip_path))

        entity, records, _invalid = chunks[0]
        assert entity == "candidacies"
        assert len(chunks) == 1
        assert [r["totalization_status"] for r in records] == ["Eleito", "Não eleito"]
        assert all(r["election_year"] == 2024 for r in records)

    def test_brasil_member_wins_over_per_uf(self, tmp_path: Path) -> None:
        """The consolidated member is parsed; per-UF files are a fallback."""
        zip_path = self._cand_zip(
            tmp_path / "consulta_cand_2024.zip",
            {
                "consulta_cand_2024_BRASIL.csv": [ELECTED_MAYOR_ROW],
                "consulta_cand_2024_PE.csv": [ELECTED_MAYOR_ROW, DEFEATED_MAYOR_ROW],
            },
        )

        chunks = list(parse_tse_zip(zip_path))

        assert sum(len(records) for _, records, _ in chunks) == 1

    def test_per_uf_members_are_the_fallback(self, tmp_path: Path) -> None:
        zip_path = self._cand_zip(
            tmp_path / "consulta_cand_2024.zip",
            {
                "consulta_cand_2024_PE.csv": [ELECTED_MAYOR_ROW],
                "consulta_cand_2024_SP.csv": [DEFEATED_MAYOR_ROW],
            },
        )

        chunks = list(parse_tse_zip(zip_path))

        assert sum(len(records) for _, records, _ in chunks) == 2


class _FakeAnchor:
    """In-memory stand-in of the frozen bronze anchor (``tse/reference/``)."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patches the lake listing/reading over the in-memory objects."""
        monkeypatch.setattr(
            "capiba.pipeline.lake.list_bronze_objects",
            lambda prefix: sorted(f"{prefix}{name}" for name in self._objects),
        )
        monkeypatch.setattr(
            "capiba.pipeline.lake.read_bronze_file",
            lambda key: self._objects[key.rsplit("/", 1)[-1]],
        )


class TestDownloadTseDump:
    """Tests for the snapshot resolver contract (frozen bronze anchor)."""

    def _zip_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("receitas_candidatos_2024_BRASIL.csv", "header")
        return buffer.getvalue()

    def _anchor(self, year: int = 2024) -> _FakeAnchor:
        return _FakeAnchor(
            {
                tse_dump_filename(year): self._zip_bytes(),
                tse_candidates_filename(year): self._zip_bytes(),
            }
        )

    def test_resolves_year_derived_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both dumps of the election year resolve from the bronze anchor."""
        self._anchor().install(monkeypatch)

        resolved = download_tse_dump(tmp_path, "2026-07", year=2024)

        assert [p.name for p in resolved] == [
            "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            "consulta_cand_2024.zip",
        ]
        assert all(p.read_bytes() == self._zip_bytes() for p in resolved)

    def test_reference_month_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fixed snapshot does not depend on the reference month."""
        self._anchor().install(monkeypatch)

        first = download_tse_dump(tmp_path / "a", "2026-01")
        second = download_tse_dump(tmp_path / "b", "2026-12")

        assert [p.name for p in first] == [p.name for p in second]

    def test_skip_and_on_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Registry resume contract: skip is honored, on_file fires per file."""
        body = self._zip_bytes()
        _FakeAnchor({"a_2024.zip": body, "b_2024.zip": body}).install(monkeypatch)
        uploaded: list[str] = []

        resolved = download_tse_dump(
            tmp_path,
            "2026-07",
            files=["a_2024.zip", "b_2024.zip"],
            skip={"a_2024.zip"},
            on_file=lambda path: uploaded.append(path.name),
        )

        assert [p.name for p in resolved] == ["b_2024.zip"]
        assert uploaded == ["b_2024.zip"]

    def test_missing_anchor_fails_with_upload_instructions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the anchor the run fails loudly, pointing to the upload."""
        _FakeAnchor({}).install(monkeypatch)

        with pytest.raises(RuntimeError, match=r"tse/reference/"):
            download_tse_dump(tmp_path, "2026-07")

        assert list(tmp_path.iterdir()) == []

    def test_wrong_year_does_not_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An anchor of another election year never satisfies the request."""
        self._anchor(year=2024).install(monkeypatch)

        with pytest.raises(RuntimeError, match=r"consulta_cand_2022"):
            download_tse_dump(tmp_path, "2026-07", year=2022)

    def test_corrupt_anchor_object_is_not_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-ZIP anchor object fails loudly instead of landing broken."""
        body = self._zip_bytes()
        _FakeAnchor(
            {
                tse_dump_filename(2024): b"<HTML><TITLE>Access Denied</TITLE>",
                tse_candidates_filename(2024): body,
            }
        ).install(monkeypatch)

        with pytest.raises(RuntimeError, match=r"not a valid ZIP"):
            download_tse_dump(tmp_path, "2026-07")


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
        assert tse_candidates_filename(2024) == "consulta_cand_2024.zip"
