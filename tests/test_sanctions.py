"""Tests for the CEIS/CNEP sanction ingestion (crawler + normalizer + tasks).

Responsibility: Validate the defensive parsing of the Portal da
Transparência sanction payloads (dates DD/MM/YYYY or ISO, Brazilian
decimals, nested payloads possibly missing), the paginated crawler
(header, cnpjSancionado filter, stop on empty page, missing API key) and
the Airflow wrappers of the entities_collect formula — all offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from capiba.ingestion.crawler_transparency import fetch_sanctions
from capiba.ingestion.sanctions import Sanction
from capiba.pipeline import lake


@pytest.fixture
def local_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Points the lake to a SQLite catalog with a local warehouse."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path}/catalog.db")
    monkeypatch.setattr(lake, "ICEBERG_LOCAL_WAREHOUSE", str(tmp_path / "warehouse"))
    lake._catalogs.clear()
    yield tmp_path
    lake._catalogs.clear()


def _ceis_payload() -> dict[str, Any]:
    """A realistic CEIS record (documented CeisDTO shape)."""
    return {
        "id": 123,
        "dataReferencia": "19/08/2026",
        "dataInicioSancao": "01/01/2025",
        "dataFimSancao": "01/01/2027",
        "dataPublicacaoSancao": "15/01/2025",
        "tipoSancao": {
            "descricaoResumida": "Inidoneidade",
            "descricaoPortal": "Inidoneidade - Legislação de Licitações",
        },
        "orgaoSancionador": {
            "nome": "MINISTERIO DA FAZENDA",
            "siglaUf": "DF",
            "poder": "EXECUTIVO",
            "esfera": "FEDERAL",
        },
        "sancionado": {
            "nome": "EMPRESA SANCIONADA LTDA",
            "codigoFormatado": "12.345.678/0001-90",
        },
        "fundamentacao": [
            {"codigo": "1", "descricao": "Lei 8.666/93, art. 87"},
            {"codigo": "2", "descricao": "Lei 12.846/13"},
        ],
        "numeroProcesso": "00426.000001/2024-01",
        "textoPublicacao": "DOU Seção 3",
        "abrangenciaDefinidaDecisaoJudicial": "NACIONAL",
    }


def _cnep_payload() -> dict[str, Any]:
    """A realistic CNEP record (CnepDTO = CeisDTO + valorMulta)."""
    return {**_ceis_payload(), "id": 456, "valorMulta": "1.234.567,89"}


class TestFetchSanctions:
    """Tests for the paginated sanction list crawler."""

    @patch("capiba.ingestion._http.requests.get")
    def test_paginates_until_empty_page(self, mock_get: MagicMock) -> None:
        """Pages are accumulated until the API returns an empty page."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [
            [{"id": 1}, {"id": 2}],
            [{"id": 3}],
            [],
        ]

        with patch(
            "capiba.ingestion.crawler_transparency.TRANSPARENCY_API_KEY",
            "test-token",
        ):
            results = fetch_sanctions("ceis")

        assert [r["id"] for r in results] == [1, 2, 3]
        pages = [c.kwargs["params"]["pagina"] for c in mock_get.call_args_list]
        assert pages == [1, 2, 3]
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["chave-api-dados"] == "test-token"

    @patch("capiba.ingestion._http.requests.get")
    def test_cnpj_filter_and_max_pages(self, mock_get: MagicMock) -> None:
        """The cnpjSancionado filter is sent and max_pages caps the walk."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [{"id": 1}]

        with patch(
            "capiba.ingestion.crawler_transparency.TRANSPARENCY_API_KEY",
            "test-token",
        ):
            results = fetch_sanctions("cnep", cnpj="12345678000190", max_pages=1)

        assert len(results) == 1
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["cnpjSancionado"] == "12345678000190"
        assert mock_get.call_count == 1

    @patch("capiba.ingestion._http.requests.get")
    def test_without_api_key(self, mock_get: MagicMock) -> None:
        """Must fail when the token is not configured."""
        with (
            patch(
                "capiba.ingestion.crawler_transparency.TRANSPARENCY_API_KEY",
                "",
            ),
            pytest.raises(RuntimeError, match="TRANSPARENCY_API_KEY"),
        ):
            fetch_sanctions("ceis")

    def test_unknown_list(self) -> None:
        """Unknown list names are rejected before any HTTP call."""
        with pytest.raises(ValueError, match="Unknown sanction list"):
            fetch_sanctions("ceaf")


class TestSanctionNormalizer:
    """Tests for the defensive CEIS/CNEP normalization."""

    def test_from_ceis_full_payload(self) -> None:
        """A full CEIS payload maps to the unified Sanction."""
        sanction = Sanction.from_ceis(_ceis_payload())

        assert sanction.id == "ceis-123"
        assert sanction.list_name == "ceis"
        assert sanction.cnpj == "12345678000190"
        assert sanction.cpf is None
        assert sanction.sanctioned_name == "EMPRESA SANCIONADA LTDA"
        assert sanction.uf == "DF"
        assert sanction.sanctioning_body == "MINISTERIO DA FAZENDA"
        assert sanction.sanction_type == "Inidoneidade - Legislação de Licitações"
        assert sanction.legal_basis == "Lei 8.666/93, art. 87; Lei 12.846/13"
        assert sanction.process_number == "00426.000001/2024-01"
        assert sanction.start_date == date(2025, 1, 1)
        assert sanction.end_date == date(2027, 1, 1)
        assert sanction.publication_date == date(2025, 1, 15)
        assert sanction.fine_amount is None

    def test_from_cnep_parses_fine_amount(self) -> None:
        """The CNEP Brazilian-decimal fine amount becomes a Decimal."""
        sanction = Sanction.from_cnep(_cnep_payload())

        assert sanction.list_name == "cnep"
        assert sanction.fine_amount == Decimal("1234567.89")

    def test_cpf_sanctioned_person(self) -> None:
        """A CPF-format sanctioned party populates cpf, not cnpj."""
        raw = _ceis_payload()
        raw["sancionado"] = {"nome": "JOAO SILVA", "codigoFormatado": "123.456.789-00"}

        sanction = Sanction.from_ceis(raw)

        assert sanction.cpf == "12345678900"
        assert sanction.cnpj is None

    def test_pessoa_fallback_for_document_and_name(self) -> None:
        """Without `sancionado`, the pessoa block provides document/name."""
        raw = _ceis_payload()
        del raw["sancionado"]
        raw["pessoa"] = {
            "cnpjFormatado": "98.765.432/0001-10",
            "razaoSocialReceita": "OUTRA EMPRESA SA",
        }

        sanction = Sanction.from_ceis(raw)

        assert sanction.cnpj == "98765432000110"
        assert sanction.sanctioned_name == "OUTRA EMPRESA SA"

    def test_missing_nested_payloads(self) -> None:
        """Missing nested blocks degrade to None instead of raising."""
        sanction = Sanction.from_ceis({"id": 7})

        assert sanction.id == "ceis-7"
        assert sanction.cnpj is None
        assert sanction.sanctioning_body is None
        assert sanction.sanction_type is None
        assert sanction.legal_basis is None
        assert sanction.start_date is None

    def test_iso_dates_accepted(self) -> None:
        """ISO dates (e.g. silver rows being revalidated) are accepted."""
        raw = {**_ceis_payload(), "dataInicioSancao": "2025-01-01"}

        assert Sanction.from_ceis(raw).start_date == date(2025, 1, 1)

    def test_unrecognized_date_and_amount_become_none(self) -> None:
        """Garbage dates/amounts degrade to None with a warning."""
        raw = {
            **_ceis_payload(),
            "dataInicioSancao": "n/a",
            "valorMulta": "sem valor",
        }

        sanction = Sanction.from_cnep(raw)

        assert sanction.start_date is None
        assert sanction.fine_amount is None

    def test_missing_id_fallback(self) -> None:
        """Without the API id, the record id falls back to the document."""
        raw = _ceis_payload()
        del raw["id"]

        assert Sanction.from_ceis(raw).id == "ceis-12345678000190"

    def test_json_roundtrip(self) -> None:
        """The JSON-mode dump revalidates against the model (lake writes)."""
        sanction = Sanction.from_cnep(_cnep_payload())
        revalidated = Sanction.model_validate(sanction.model_dump(mode="json"))

        assert revalidated == sanction


class TestEntityTasks:
    """Tests for the Airflow wrappers of the entities_collect formula."""

    def _spec_file(self, tmp_path: Path) -> Path:
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            """\
name: sanctions_task
window: all
sources: [ceis, cnep]
formula: entities_collect
destinations: [lake_bronze, lake_silver]
""",
            encoding="utf-8",
        )
        return spec_path

    def test_normalize_entities_writes_silver(
        self, tmp_path: Path, local_catalog: Path
    ) -> None:
        """Raw XCom records are normalized and appended to the silver table."""
        from capiba.pipeline.entity_tasks import task_normalize_entities

        ti = MagicMock()
        ti.xcom_pull.return_value = [_ceis_payload()]

        summary = task_normalize_entities(
            "ceis", str(self._spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert summary == {
            "source": "ceis",
            "entities": {"sanctions": 1},
            "errors": 0,
        }
        ti.xcom_push.assert_called_once_with(key="entities_ceis", value=summary)
        rows = [r for batch in lake.read_silver_entities("sanctions") for r in batch]
        assert len(rows) == 1
        assert rows[0]["cnpj"] == "12345678000190"
        assert rows[0]["list_name"] == "ceis"
        assert str(rows[0]["dt"]) == "2026-08-18"

    def test_normalize_entities_best_effort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing silver write is counted, not fatal to the task."""
        from capiba.pipeline.entity_tasks import task_normalize_entities

        monkeypatch.setattr(
            lake,
            "write_silver_entities",
            MagicMock(side_effect=RuntimeError("lake down")),
        )
        ti = MagicMock()
        ti.xcom_pull.return_value = [_ceis_payload()]

        summary = task_normalize_entities(
            "ceis", str(self._spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert summary["entities"] == {"sanctions": 1}
        assert summary["errors"] == 1

    def test_silver_entities_summary(self, tmp_path: Path) -> None:
        """The silver destination reports the per-source normalize counts."""
        from capiba.pipeline.entity_tasks import task_silver_entities_summary

        ti = MagicMock()
        ti.xcom_pull.return_value = {
            "source": "ceis",
            "entities": {"sanctions": 3},
            "errors": 0,
        }

        summary = task_silver_entities_summary(
            str(self._spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert summary == {
            "entities": {
                "ceis": {"source": "ceis", "entities": {"sanctions": 3}, "errors": 0},
                "cnep": {"source": "ceis", "entities": {"sanctions": 3}, "errors": 0},
            }
        }
