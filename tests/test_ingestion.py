"""Tests for the ingestion vertical slice.

Responsibility: Validate crawlers, normalizer, validator and persistence.
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from capiba.ingestion.crawler_federal_revenue import (
    _build_download_url,
    download_cnpj_dump,
    extract_cnpj_zip,
    parse_cnpj_csv,
)
from capiba.ingestion.crawler_pncp import fetch_contract_updates, fetch_contracts
from capiba.ingestion.crawler_transparency import (
    fetch_contracts as fetch_contracts_transparency,
)
from capiba.ingestion.normalizer import (
    Contract,
    _extract_supplier,
    _generate_fallback_id,
    _modality_by_code,
    _parse_date,
    _parse_decimal,
    _status_by_code,
)
from capiba.ingestion.persistence import (
    _sanitize_key,
    _schema_hash,
    bulk_upsert_cnpj,
    bulk_upsert_contracts,
    upsert_contract,
)
from capiba.ingestion.validator import checksum, detect_duplicates


def _pncp_payload() -> dict[str, object]:
    """Returns a realistic payload from the /v1/contratacoes/publicacao endpoint."""
    return {
        "numeroControlePNCP": "12345678000190-1-000001/2026",
        "numeroCompra": "001/2026",
        "anoCompra": 2026,
        "processo": "P001/2026",
        "modalidadeId": 6,
        "modalidadeNome": "Pregão - Eletrônico",
        "situacaoCompraId": 1,
        "situacaoCompraNome": "Divulgada no PNCP",
        "objetoCompra": "Aquisição de material de escritório",
        "valorTotalHomologado": 15000.00,
        "dataPublicacaoPncp": "2026-01-15",
        "dataAberturaProposta": "2026-01-20T10:00:00",
        "orgaoEntidade": {
            "cnpj": "12345678000190",
            "razaosocial": "Prefeitura Municipal de Exemplo",
            "esferaId": "M",
        },
        "unidadeOrgao": {
            "codigoUnidade": "123456",
            "nomeUnidade": "Secretaria Municipal de Administração",
            "ufSigla": "MG",
            "municipioNome": "Belo Horizonte",
        },
    }


def _pncp_contract_payload() -> dict[str, object]:
    """Returns a realistic payload from the /v1/contratos endpoint."""
    return {
        "numeroControlePNCP": "12345678000190-2-000001/2026",
        "numeroContratoEmpenho": "001/2026",
        "anoContrato": 2026,
        "processo": "P001/2026",
        "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"},
        "objetoContrato": "Aquisição de material de escritório",
        "valorInicial": 15000.00,
        "valorGlobal": 15000.00,
        "dataAssinatura": "2026-01-15",
        "dataVigenciaInicio": "2026-01-15",
        "dataVigenciaFim": "2026-12-31",
        "dataPublicacaoPncp": "2026-01-15T10:00:00",
        "tipoPessoa": "PJ",
        "niFornecedor": "98765432000196",
        "nomeRazaoSocialFornecedor": "Fornecedora Exemplo Ltda",
        "orgaoEntidade": {
            "cnpj": "12345678000190",
            "razaoSocial": "Prefeitura Municipal de Exemplo",
            "esferaId": "M",
        },
        "unidadeOrgao": {
            "codigoUnidade": "123456",
            "nomeUnidade": "Secretaria Municipal de Administração",
            "ufSigla": "MG",
            "municipioNome": "Belo Horizonte",
        },
    }


def _transparency_payload() -> dict[str, object]:
    """Returns a realistic payload from the Portal da Transparência."""
    return {
        "id": "T001",
        "numeroContrato": "001/2026",
        "numeroProcesso": "P002/2026",
        "objeto": "Serviços de limpeza",
        "valorInicial": 50000.00,
        "dataAssinatura": "2026-02-01",
        "dataVigenciaInicio": "2026-02-01",
        "dataVigenciaFim": "2026-12-31",
        "modalidade": "Dispensa",
        "situacao": "concluido",
        "orgao": {
            "codigoSIAFI": "123456",
            "nome": "Prefeitura Municipal de Exemplo",
            "esfera": "municipal",
            "uf": "MG",
            "municipio": "Belo Horizonte",
        },
        "fornecedor": {
            "cnpj": "98765432000196",
            "razaoSocial": "Limpeza Total Ltda",
        },
    }


class TestNormalizer:
    """Tests for the unified entity schema."""

    def test_contract_from_pncp(self) -> None:
        """Contract.from_pncp must map the PNCP payload correctly."""
        raw = _pncp_payload()
        contract = Contract.from_pncp(raw)

        assert contract.id == "12345678000190-1-000001/2026"
        assert contract.process_number == "P001/2026"
        assert contract.subject == "Aquisição de material de escritório"
        assert contract.amount == Decimal("15000.00")
        assert contract.modality == "pregão - eletrônico"
        assert contract.status == "divulgada no pncp"
        assert contract.buyer.name == "Secretaria Municipal de Administração"
        assert contract.buyer.government_level == "municipal"
        assert contract.buyer.uf == "MG"

    def test_contract_from_transparency(self) -> None:
        """Contract.from_transparency must map the Portal da Transparência payload."""
        raw = _transparency_payload()
        contract = Contract.from_transparency(raw)

        assert contract.id == "T001"
        assert contract.amount == Decimal("50000.00")
        assert contract.supplier.cnpj == "98765432000196"
        assert contract.supplier.legal_name == "Limpeza Total Ltda"
        assert contract.buyer.government_level == "municipal"

    def test_contract_from_pncp_supplier_not_informed(self) -> None:
        """Must accept procurements without an explicit supplier."""
        raw = _pncp_payload()
        contract = Contract.from_pncp(raw)

        assert contract.supplier.cnpj is None
        assert contract.supplier.legal_name == "Supplier not informed"

    def test_contract_from_pncp_contract(self) -> None:
        """Must map the /v1/contratos endpoint payload with supplier."""
        raw = _pncp_contract_payload()
        contract = Contract.from_pncp(raw)

        assert contract.id == "12345678000190-2-000001/2026"
        assert contract.supplier.cnpj == "98765432000196"
        assert contract.supplier.legal_name == "Fornecedora Exemplo Ltda"
        assert contract.modality == "contrato (termo inicial)"
        assert contract.status == "published"


class TestCrawlerPNCP:
    """Tests for the PNCP crawler."""

    @patch("capiba.ingestion._http.requests.get")
    def test_fetch_contracts_pagination(self, mock_get: MagicMock) -> None:
        """Must iterate over contract pages."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [
            {
                "data": [{"numeroControlePNCP": "C1"}],
                "paginasRestantes": 1,
                "numeroPagina": 1,
            },
            {
                "data": [{"numeroControlePNCP": "C2"}],
                "paginasRestantes": 0,
                "numeroPagina": 2,
            },
        ]

        results = fetch_contracts("2026-01-01", "2026-01-01")

        assert len(results) == 2
        assert results[0]["numeroControlePNCP"] == "C1"
        assert results[1]["numeroControlePNCP"] == "C2"

    @patch("capiba.ingestion._http.requests.get")
    def test_fetch_contracts_accepts_date(self, mock_get: MagicMock) -> None:
        """Must accept date objects and format them as yyyyMMdd."""
        from datetime import date

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "data": [{"numeroControlePNCP": "C1"}],
            "paginasRestantes": 0,
            "numeroPagina": 1,
        }

        fetch_contracts(date(2026, 1, 1), date(2026, 1, 2))

        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["dataInicial"] == "20260101"
        assert params["dataFinal"] == "20260102"

    @patch("capiba.ingestion._http.requests.get")
    def test_fetch_contract_updates_uses_update_endpoint(
        self, mock_get: MagicMock
    ) -> None:
        """Must hit /v1/contratos/atualizacao with the formatted window."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "data": [{"numeroControlePNCP": "U1"}],
            "paginasRestantes": 0,
            "numeroPagina": 1,
        }

        results = fetch_contract_updates("2026-01-01", "2026-01-02")

        assert results[0]["numeroControlePNCP"] == "U1"
        args, kwargs = mock_get.call_args
        assert args[0].endswith("/v1/contratos/atualizacao")
        assert kwargs["params"]["dataInicial"] == "20260101"
        assert kwargs["params"]["dataFinal"] == "20260102"

    @patch("capiba.ingestion._http.requests.get")
    def test_fetch_contract_updates_pagination(self, mock_get: MagicMock) -> None:
        """Must iterate over update pages."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [
            {
                "data": [{"numeroControlePNCP": "U1"}],
                "paginasRestantes": 1,
                "numeroPagina": 1,
            },
            {
                "data": [{"numeroControlePNCP": "U2"}],
                "paginasRestantes": 0,
                "numeroPagina": 2,
            },
        ]

        results = fetch_contract_updates("2026-01-01", "2026-01-01")

        assert [r["numeroControlePNCP"] for r in results] == ["U1", "U2"]


class TestCrawlerTransparency:
    """Tests for the Portal da Transparência crawler."""

    @patch("capiba.ingestion._http.requests.get")
    def test_fetch_contracts_sends_header(self, mock_get: MagicMock) -> None:
        """Must send the authentication header."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [{"id": "T001"}]

        with patch(
            "capiba.ingestion.crawler_transparency.TRANSPARENCY_API_KEY",
            "test-token",
        ):
            results = fetch_contracts_transparency("2026-01-01", "2026-01-01")

        assert len(results) == 1
        assert results[0]["id"] == "T001"
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["chave-api-dados"] == "test-token"

    @patch("capiba.ingestion._http.requests.get")
    def test_fetch_contracts_without_token(self, mock_get: MagicMock) -> None:
        """Must fail when the token is not configured."""
        with (
            patch(
                "capiba.ingestion.crawler_transparency.TRANSPARENCY_API_KEY",
                "",
            ),
            pytest.raises(RuntimeError),
        ):
            fetch_contracts_transparency("2026-01-01", "2026-01-01")


class TestCrawlerFederalRevenue:
    """Tests for the Federal Revenue crawler."""

    @patch("capiba.ingestion.crawler_federal_revenue.requests.get")
    def test_download_cnpj_dump(self, mock_get: MagicMock, tmp_path: Path) -> None:
        """Must download the listed files."""
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"PK"]
        mock_get.return_value.__enter__.return_value = mock_response

        destination = tmp_path / "cnpj"
        files = download_cnpj_dump(
            destination=destination,
            reference_month="2025-01",
            files=["Cnaes.zip"],
        )

        assert len(files) == 1
        assert files[0].name == "Cnaes.zip"

    @patch("capiba.ingestion.crawler_federal_revenue.requests.get")
    def test_download_skips_bronze_uploaded_files(
        self, mock_get: MagicMock, tmp_path: Path
    ) -> None:
        """Files in the skip set are not downloaded; on_file fires per file."""
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b"PK"]
        mock_get.return_value.__enter__.return_value = mock_response
        seen: list[str] = []

        files = download_cnpj_dump(
            destination=tmp_path / "cnpj",
            reference_month="2025-01",
            files=["Cnaes.zip", "Paises.zip"],
            skip={"Paises.zip"},
            on_file=lambda p: seen.append(p.name),
        )

        assert [f.name for f in files] == ["Cnaes.zip"]
        assert seen == ["Cnaes.zip"]
        mock_get.assert_called_once()

    @patch("capiba.ingestion.crawler_federal_revenue.requests.get")
    def test_download_rejects_invalid_zip(
        self, mock_get: MagicMock, tmp_path: Path
    ) -> None:
        """Empty/HTML payloads (share error pages with HTTP 200) are dropped."""
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b""]
        mock_get.return_value.__enter__.return_value = mock_response

        files = download_cnpj_dump(
            destination=tmp_path / "cnpj",
            reference_month="2025-01",
            files=["Cnaes.zip"],
        )

        assert files == []
        assert not (tmp_path / "cnpj" / "Cnaes.zip").exists()


class TestValidator:
    """Data validation tests."""

    def test_checksum(self) -> None:
        """Checksum must be deterministic and content-sensitive."""
        data = b"checksum test"
        assert checksum(data) == checksum(data)
        assert checksum(b"data A") != checksum(b"data B")

    def test_detect_duplicates(self, sample_contracts: list[dict[str, object]]) -> None:
        """Must detect duplicated keys."""
        duplicated = sample_contracts + sample_contracts[:1]
        dups = detect_duplicates(duplicated, key="id")
        assert "C001" in dups

    def test_no_duplicates(self, sample_contracts: list[dict[str, object]]) -> None:
        """Must not report false positives."""
        dups = detect_duplicates(sample_contracts, key="id")
        assert len(dups) == 0


class TestPersistence:
    """ArangoDB persistence tests."""

    def test_sanitize_key(self) -> None:
        """Must sanitize invalid _key characters."""
        assert _sanitize_key("123/abc") == "123_abc"
        assert _sanitize_key("x" * 300) == "x" * 254

    @patch("capiba.ingestion.persistence.upsert_vertex")
    @patch("capiba.ingestion.persistence.upsert_edge")
    def test_upsert_contract_creates_edge(
        self,
        mock_edge: MagicMock,
        mock_vertex: MagicMock,
    ) -> None:
        """Must persist the contract and create a won edge."""
        raw = _transparency_payload()
        contract = Contract.from_transparency(raw)

        db = MagicMock()
        upsert_contract(db, contract)

        assert mock_vertex.call_count == 3  # contract + supplier + buyer
        mock_edge.assert_called_once()
        args = mock_edge.call_args[0]
        assert args[1] == "won"
        assert args[2] == "suppliers/98765432000196"
        assert args[3] == "contracts/T001"


class TestNormalizerHelpers:
    """Tests for the normalizer helper functions and edge cases."""

    def test_from_pncp_without_amount_defaults_to_zero(self) -> None:
        """Must default the amount to zero when no value field is present."""
        raw = {"numeroControlePNCP": "C-1", "objetoCompra": "Sem valor"}
        contract = Contract.from_pncp(raw)

        assert contract.amount == Decimal("0")

    def test_from_transparency_without_amount_defaults_to_zero(self) -> None:
        """Must default the amount to zero when no value field is present."""
        raw = {"id": "T2", "objeto": "Sem valor"}
        contract = Contract.from_transparency(raw)

        assert contract.amount == Decimal("0")

    def test_fallback_id_from_available_fields(self) -> None:
        """Must compose the fallback ID from cnpj, year and sequential."""
        raw = {
            "orgaoEntidade": {"cnpj": "12345678000190"},
            "anoCompra": 2026,
            "sequencialCompra": 7,
        }
        assert _generate_fallback_id(raw) == "12345678000190-2026-7"

    def test_fallback_id_unknown_when_empty(self) -> None:
        """Must return 'unknown' when no field is available."""
        assert _generate_fallback_id({}) == "unknown"

    def test_from_pncp_uses_fallback_id(self) -> None:
        """Must use the fallback ID when no control number exists."""
        raw = {
            "anoCompra": 2026,
            "orgaoEntidade": {"cnpj": "12345678000190"},
        }
        contract = Contract.from_pncp(raw)

        assert contract.id == "12345678000190-2026"

    def test_extract_supplier_with_cpf(self) -> None:
        """Must map an 11-digit identifier to the CPF field."""
        supplier = _extract_supplier(
            {},
            {"cpfFornecedor": "123.456.789-01", "nomeFornecedor": "Fulano"},
        )

        assert supplier.cpf == "12345678901"
        assert supplier.cnpj is None
        assert supplier.legal_name == "Fulano"

    def test_parse_decimal(self) -> None:
        """Must convert values to Decimal defensively."""
        assert _parse_decimal(None) is None
        assert _parse_decimal(Decimal("1.5")) == Decimal("1.5")
        assert _parse_decimal("1234,56") == Decimal("1234.56")
        assert _parse_decimal("not-a-number") is None

    def test_parse_date_accepts_date_instance(self) -> None:
        """Must return date objects unchanged."""
        assert _parse_date(date(2026, 1, 15)) == date(2026, 1, 15)

    def test_parse_date_fallback_regex(self) -> None:
        """Must extract an ISO date embedded in a longer string."""
        assert _parse_date("publicado em 2026-01-15 (retificado)") == date(2026, 1, 15)

    def test_parse_date_unparseable(self) -> None:
        """Must return None for values without a recognizable date."""
        assert _parse_date(None) is None
        assert _parse_date("") is None
        assert _parse_date("sem data") is None

    def test_modality_by_code(self) -> None:
        """Must map PNCP modality codes to names."""
        assert _modality_by_code(6) == "pregao_eletronico"
        assert _modality_by_code(13) == "leilao_presencial"
        assert _modality_by_code(999) == "not_informed"
        assert _modality_by_code(None) == "not_informed"

    def test_from_pncp_modality_by_code(self) -> None:
        """Must derive the modality from the code when no name is given."""
        raw = {"numeroControlePNCP": "C-1", "modalidadeId": 8}
        contract = Contract.from_pncp(raw)

        assert contract.modality == "dispensa"

    def test_status_by_code(self) -> None:
        """Must map PNCP status codes to names."""
        assert _status_by_code(None) is None
        assert _status_by_code(1) == "published"
        assert _status_by_code(2) == "revoked"
        assert _status_by_code(3) == "annulled"
        assert _status_by_code(4) == "suspended"
        assert _status_by_code(99) is None

    def test_from_pncp_status_by_code(self) -> None:
        """Must derive the status from the code when no name is given."""
        raw = {"numeroControlePNCP": "C-1", "situacaoCompraId": 3}
        contract = Contract.from_pncp(raw)

        assert contract.status == "annulled"


class TestCrawlerFederalRevenueHelpers:
    """Tests for the Federal Revenue crawler helpers."""

    def test_build_download_url_with_path(self) -> None:
        """Must build a public WebDAV URL with the reference month path."""
        url = _build_download_url(
            "https://share.example.com/index.php/s/abc/download",
            "/2025-01/",
            "Cnaes.zip",
        )
        assert (
            url
            == "https://share.example.com/public.php/dav/files/abc/2025-01/Cnaes.zip"
        )

    def test_build_download_url_without_path(self) -> None:
        """Must build a root WebDAV URL when the path is empty."""
        url = _build_download_url(
            "https://share.example.com/index.php/s/abc/download", "", "Cnaes.zip"
        )
        assert url == "https://share.example.com/public.php/dav/files/abc/Cnaes.zip"

    def test_build_download_url_accepts_dav_base(self) -> None:
        """A base URL already in the public DAV form is used as-is."""
        url = _build_download_url(
            "https://share.example.com/public.php/dav/files/abc/",
            "/2025-01/",
            "Cnaes.zip",
        )
        assert (
            url
            == "https://share.example.com/public.php/dav/files/abc/2025-01/Cnaes.zip"
        )

    @patch("capiba.ingestion.crawler_federal_revenue.requests.get")
    def test_download_skips_existing_file(
        self, mock_get: MagicMock, tmp_path: Path
    ) -> None:
        """Must not download files that already exist locally."""
        destination = tmp_path / "cnpj"
        destination.mkdir()
        existing = destination / "Cnaes.zip"
        existing.write_bytes(b"PK")

        files = download_cnpj_dump(
            destination=destination,
            reference_month="2025-01",
            files=["Cnaes.zip"],
        )

        assert files == [existing]
        mock_get.assert_not_called()

    @patch("capiba.ingestion.crawler_federal_revenue.requests.get")
    def test_download_network_error_is_logged(
        self, mock_get: MagicMock, tmp_path: Path
    ) -> None:
        """Network failures must be skipped without aborting the batch."""
        mock_get.return_value.__enter__.side_effect = requests.ConnectionError("down")

        files = download_cnpj_dump(
            destination=tmp_path / "cnpj",
            reference_month="2025-01",
            files=["Cnaes.zip", "Paises.zip"],
        )

        assert files == []

    def test_extract_cnpj_zip_only_csvs(self, tmp_path: Path) -> None:
        """Must extract only the CSV entries from the ZIP."""
        zip_path = tmp_path / "Cnaes.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Cnaes.csv", "1;Foo")
            zf.writestr("readme.txt", "ignore me")

        destination = tmp_path / "out"
        extracted = extract_cnpj_zip(zip_path, destination)

        assert extracted == [destination / "Cnaes.csv"]
        assert (destination / "Cnaes.csv").read_text() == "1;Foo"
        assert not (destination / "readme.txt").exists()

    def test_extract_cnpj_zip_default_destination(self, tmp_path: Path) -> None:
        """Must extract into the ZIP's own directory by default."""
        zip_path = tmp_path / "Paises.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Paises.csv", "1;Brasil")

        extracted = extract_cnpj_zip(zip_path)

        assert extracted == [tmp_path / "Paises.csv"]

    def test_parse_cnpj_csv(self, tmp_path: Path) -> None:
        """Must parse a semicolon-separated latin1 CSV as strings."""
        csv_file = tmp_path / "Cnaes.csv"
        csv_file.write_bytes("10;Indústria\n20;Comércio\n".encode("latin1"))

        df = parse_cnpj_csv(csv_file)

        assert df.shape == (2, 2)
        assert df.iloc[1, 1] == "Comércio"
        assert isinstance(df.iloc[0, 0], str)


class TestPersistenceBulk:
    """Tests for bulk persistence and lineage registration."""

    def test_sanitize_key_empty(self) -> None:
        """Must never return an empty _key."""
        assert _sanitize_key("///") == "_"
        assert _sanitize_key("") == "_"

    @patch("capiba.ingestion.persistence.upsert_vertex")
    @patch("capiba.ingestion.persistence.upsert_edge")
    def test_upsert_contract_registers_lineage(
        self,
        mock_edge: MagicMock,
        mock_vertex: MagicMock,
    ) -> None:
        """Must register the contract in the lineage tracker when given."""
        contract = Contract.from_transparency(_transparency_payload())
        tracker = MagicMock()

        upsert_contract(MagicMock(), contract, tracker=tracker)

        tracker.register_dataset.assert_called_once()
        kwargs = tracker.register_dataset.call_args.kwargs
        assert kwargs["name"] == "contract_T001"
        assert kwargs["row_count"] == 1
        expected_hash = hashlib.sha256(
            ",".join(Contract.model_fields.keys()).encode()
        ).hexdigest()
        assert kwargs["schema_hash"] == expected_hash

    def test_schema_hash_is_deterministic(self) -> None:
        """Must hash the contract schema field names."""
        contract = Contract.from_transparency(_transparency_payload())
        assert _schema_hash(contract) == _schema_hash(contract)
        assert len(_schema_hash(contract)) == 64

    @patch("capiba.ingestion.persistence.upsert_contract")
    def test_bulk_upsert_counts_errors(self, mock_upsert: MagicMock) -> None:
        """Must continue past failures and summarize successes and errors."""
        mock_upsert.side_effect = [
            {"contract_key": "ok"},
            Exception("db down"),
            {"contract_key": "ok2"},
        ]
        contracts = [
            Contract.from_transparency(_transparency_payload()),
            Contract.from_transparency({**_transparency_payload(), "id": "T002"}),
            Contract.from_transparency({**_transparency_payload(), "id": "T003"}),
        ]

        summary = bulk_upsert_contracts(MagicMock(), contracts)

        assert summary == {"total": 3, "succeeded": 2, "errors": 1}
        assert mock_upsert.call_count == 3


class TestBulkUpsertCnpj:
    """Tests for the CNPJ graph bulk load (companies/partners/partner_of)."""

    def _db(self) -> tuple[MagicMock, dict[str, MagicMock]]:
        """A mocked db whose collections are stable per-name mocks."""
        db = MagicMock()
        collections: dict[str, MagicMock] = {}
        db.collection.side_effect = lambda name: collections.setdefault(
            name, MagicMock(name=name)
        )
        return db, collections

    def test_company_vertices_keyed_by_cnpj_basico(self) -> None:
        """Company vertices are imported in bulk with cnpj_basico keys."""
        db, collections = self._db()

        summary = bulk_upsert_cnpj(
            db,
            companies=[
                {"cnpj_basico": "12345678", "razao_social": "ACME", "dt": "2026-01-15"},
                {"cnpj_basico": "87654321", "razao_social": "BETA"},
            ],
            partners=[],
        )

        assert summary == {"companies": 2, "partners": 0, "edges": 0, "errors": 0}
        import_bulk = collections["companies"].import_bulk
        docs = import_bulk.call_args.args[0]
        assert [d["_key"] for d in docs] == ["12345678", "87654321"]
        assert docs[0]["razao_social"] == "ACME"
        assert "dt" not in docs[0]  # lake metadata stays out of the vertex
        assert import_bulk.call_args.kwargs == {"on_duplicate": "replace"}

    def test_partner_vertices_and_edges(self) -> None:
        """Partner vertices yield partner_of edges into their companies."""
        db, collections = self._db()

        summary = bulk_upsert_cnpj(
            db,
            companies=[],
            partners=[
                {
                    "partner_id": "p1" * 16,
                    "cnpj_basico": "12345678",
                    "nome": "JOAO SILVA",
                    "qualificacao": "22",
                }
            ],
        )

        assert summary == {"companies": 0, "partners": 1, "edges": 1, "errors": 0}
        partner_doc = collections["partners"].import_bulk.call_args.args[0][0]
        assert partner_doc["_key"] == "p1" * 16
        assert partner_doc["nome"] == "JOAO SILVA"
        edge_doc = collections["partner_of"].import_bulk.call_args.args[0][0]
        assert edge_doc["_from"] == f"partners/{'p1' * 16}"
        assert edge_doc["_to"] == "companies/12345678"
        assert edge_doc["qualificacao"] == "22"

    def test_partner_key_fallback_without_partner_id(self) -> None:
        """Rows without partner_id get the hash key (never the masked doc)."""
        from capiba.ingestion.cnpj import partner_key

        db, collections = self._db()

        bulk_upsert_cnpj(
            db,
            companies=[],
            partners=[
                {"cnpj_basico": "12345678", "nome": "JOAO", "qualificacao": "22"}
            ],
        )

        partner_doc = collections["partners"].import_bulk.call_args.args[0][0]
        assert partner_doc["_key"] == partner_key("12345678", "JOAO", "22")

    def test_batch_failures_are_counted(self) -> None:
        """A failed import_bulk is an error count, not an exception."""
        db, collections = self._db()
        db.collection("companies").import_bulk.side_effect = RuntimeError("db down")

        summary = bulk_upsert_cnpj(
            db, companies=[{"cnpj_basico": "12345678"}], partners=[]
        )

        assert summary == {"companies": 0, "partners": 0, "edges": 0, "errors": 1}

    def test_batching_splits_imports(self) -> None:
        """Items larger than batch_size are split into multiple imports."""
        db, collections = self._db()
        companies = [{"cnpj_basico": f"{i:08d}"} for i in range(5)]

        summary = bulk_upsert_cnpj(db, companies=companies, partners=[], batch_size=2)

        assert summary["companies"] == 5
        assert collections["companies"].import_bulk.call_count == 3
