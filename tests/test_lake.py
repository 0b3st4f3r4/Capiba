"""Tests for the medallion lake writer.

Responsibility: Validate the partitioned object key layout and the
gzip + JSON round-trip for the raw audit copies (bronze/gold, MinIO
mocked), plus the Iceberg table round-trip for bronze and silver using
a SQLite catalog with a local filesystem warehouse (no infra needed).
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba import config
from capiba.pipeline import lake, trino

RUN_DATE = date(2026, 1, 15)


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replaces the lazy lake MinIO client factory with a mock."""
    client = MagicMock()
    monkeypatch.setattr(lake, "get_client", lambda: client)
    return client


@pytest.fixture
def local_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Points the lake to a SQLite catalog with a local warehouse."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path}/catalog.db")
    monkeypatch.setattr(lake, "ICEBERG_LOCAL_WAREHOUSE", str(tmp_path / "warehouse"))
    lake._catalogs.clear()
    yield tmp_path
    lake._catalogs.clear()


def _put_call(client: MagicMock) -> tuple[str, str, bytes]:
    """Extracts (bucket, key, body) from the mock's put_object call."""
    assert client.put_object.call_count == 1
    args, _ = client.put_object.call_args
    bucket, key, stream = args[0], args[1], args[2]
    return bucket, key, stream.read()


def _contract(identifier: str) -> dict[str, Any]:
    """Builds a minimal Contract-valid serializable record."""
    return {
        "id": identifier,
        "process_number": "P-001",
        "subject": "Office supplies",
        "amount": "1234.56",
        "signature_date": "2026-01-10",
        "validity_start": "2026-01-10",
        "validity_end": "2026-12-31",
        "buyer": {
            "siafi_code": "26000",
            "name": "City Hall",
            "government_level": "municipal",
            "uf": "PE",
            "city": "Recife",
        },
        "supplier": {
            "cnpj": "12345678000199",
            "cpf": None,
            "legal_name": "Supplier Ltda",
            "trade_name": None,
            "primary_cnae": None,
            "state": "PE",
            "city": None,
        },
        "modality": "pregao_eletronico",
        "status": "completed",
    }


def test_write_bronze_key_layout_and_roundtrip(mock_client: MagicMock) -> None:
    """Bronze writes gzip JSON under <source>/dt=<date>/."""
    payload: Any = [{"id": "1", "valor": 10.5}]

    key = lake.write_bronze("pncp", payload, run_date=RUN_DATE)

    assert re.fullmatch(r"pncp/dt=2026-01-15/\d{8}T\d{6}-[0-9a-f]{8}\.json\.gz", key)
    bucket, written_key, body = _put_call(mock_client)
    assert bucket == config.LAKE_BUCKET_BRONZE
    assert written_key == key
    assert json.loads(gzip.decompress(body)) == payload


def test_write_bronze_defaults_to_today(mock_client: MagicMock) -> None:
    """Without run_date, the partition uses today's date (UTC)."""
    key = lake.write_bronze("transparency", [])

    today = datetime.now(UTC).date().isoformat()
    assert key.startswith(f"transparency/dt={today}/")


def test_write_gold_key_layout_and_roundtrip(mock_client: MagicMock) -> None:
    """Gold writes gzip JSON under reports/<name>/dt=<date>/."""
    report = {"total": 2, "valid": True}

    key = lake.write_gold(report, "daily_ingestion", run_date=RUN_DATE)

    assert re.fullmatch(
        r"reports/daily_ingestion/dt=2026-01-15/\d{8}T\d{6}\.json\.gz",
        key,
    )
    bucket, written_key, body = _put_call(mock_client)
    assert bucket == config.LAKE_BUCKET_GOLD
    assert written_key == key
    assert json.loads(gzip.decompress(body)) == report


def test_write_keys_are_unique(mock_client: MagicMock) -> None:
    """Consecutive bronze writes for the same partition get unique keys."""
    first = lake.write_bronze("pncp", [], run_date=RUN_DATE)
    mock_client.put_object.reset_mock()
    second = lake.write_bronze("pncp", [], run_date=RUN_DATE)

    assert first != second


def test_write_bronze_table_roundtrip(local_catalog: Path) -> None:
    """Bronze Iceberg table keeps one JSON payload row per run."""
    payload = [{"id": "1", "valor": 10.5}]

    identifier = lake.write_bronze_table("pncp", payload, run_date=RUN_DATE)

    assert identifier == "capiba.raw_pncp"
    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_BRONZE).load_table(identifier)
    rows = table.scan().to_arrow()
    assert rows.num_rows == 1
    assert json.loads(rows.column("payload_json")[0].as_py()) == payload
    assert rows.column("dt")[0].as_py() == RUN_DATE


def test_write_silver_roundtrip_typed(local_catalog: Path) -> None:
    """Silver Iceberg table stores typed contracts partitioned by dt."""
    identifier = lake.write_silver(
        [_contract("C001"), _contract("C002")], run_date=RUN_DATE
    )

    assert identifier == "capiba.contracts"
    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(identifier)
    rows = table.scan().to_arrow()
    assert rows.num_rows == 2
    assert rows.column("amount")[0].as_py() == Decimal("1234.56")
    assert rows.column("signature_date")[0].as_py() == date(2026, 1, 10)
    assert rows.column("buyer")[0].as_py()["siafi_code"] == "26000"
    assert rows.column("dt")[0].as_py() == RUN_DATE


def test_write_silver_appends_and_skips_invalid(local_catalog: Path) -> None:
    """Appends accumulate rows; invalid records are skipped, not fatal."""
    lake.write_silver([_contract("C001")], run_date=RUN_DATE)
    lake.write_silver([{"id": "broken"}, _contract("C002")], run_date=RUN_DATE)

    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(
        "capiba.contracts"
    )
    assert table.scan().to_arrow().num_rows == 2


def test_silver_table_has_dt_partition_spec(local_catalog: Path) -> None:
    """The silver table is created partitioned by identity(dt)."""
    lake.write_silver([_contract("C001")], run_date=RUN_DATE)

    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(
        "capiba.contracts"
    )
    spec = table.spec()
    assert len(spec.fields) == 1
    assert spec.fields[0].name == "dt"


def test_write_bronze_file_key_layout_and_raw_bytes(mock_client: MagicMock) -> None:
    """Bronze file uploads keep raw bytes under <source>/files/dt=<date>/."""
    data = b"\x50\x4b fake-zip-bytes"

    key = lake.write_bronze_file("federal_revenue", "dump.zip", data, run_date=RUN_DATE)

    assert key == "federal_revenue/files/dt=2026-01-15/dump.zip"
    bucket, written_key, body = _put_call(mock_client)
    assert bucket == config.LAKE_BUCKET_BRONZE
    assert written_key == key
    assert body == data


def test_get_catalog_sqlite_requires_local_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQLite catalog URI without a local warehouse is a config error."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path}/catalog.db")
    monkeypatch.setattr(lake, "ICEBERG_LOCAL_WAREHOUSE", "")
    lake._catalogs.pop("no_warehouse", None)

    with pytest.raises(ValueError, match="ICEBERG_LOCAL_WAREHOUSE"):
        lake.get_catalog("no_warehouse")


def test_get_catalog_rest_without_oauth2(monkeypatch: pytest.MonkeyPatch) -> None:
    """REST catalog config carries no OAuth2 keys when credentials are unset."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", "http://lakekeeper:8181/catalog")
    monkeypatch.setattr(lake, "ICEBERG_OAUTH2_CLIENT_ID", "")
    monkeypatch.setattr(lake, "ICEBERG_OAUTH2_CLIENT_SECRET", "")
    lake._catalogs.pop("rest_plain", None)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        lake, "load_catalog", lambda _name, **cfg: captured.setdefault("cfg", cfg)
    )

    lake.get_catalog("rest_plain")

    cfg = captured["cfg"]
    assert cfg["type"] == "rest"
    assert "credential" not in cfg
    assert "oauth2-server-uri" not in cfg


def test_get_catalog_rest_with_oauth2(monkeypatch: pytest.MonkeyPatch) -> None:
    """REST catalog config passes client credentials to the OAuth2 endpoint."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", "http://lakekeeper:8181/catalog")
    monkeypatch.setattr(lake, "ICEBERG_OAUTH2_CLIENT_ID", "capiba-services")
    monkeypatch.setattr(lake, "ICEBERG_OAUTH2_CLIENT_SECRET", "s3cret")
    monkeypatch.setattr(lake, "ICEBERG_OAUTH2_SERVER_URI", "http://keycloak:8080/token")
    lake._catalogs.pop("rest_oauth", None)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        lake, "load_catalog", lambda _name, **cfg: captured.setdefault("cfg", cfg)
    )

    lake.get_catalog("rest_oauth")

    cfg = captured["cfg"]
    assert cfg["credential"] == "capiba-services:s3cret"
    assert cfg["oauth2-server-uri"] == "http://keycloak:8080/token"


def test_read_silver_contracts_roundtrip(local_catalog: Path) -> None:
    """Reads back every typed row written to the silver contracts table."""
    lake.write_silver([_contract("C001"), _contract("C002")], run_date=RUN_DATE)

    rows = lake.read_silver_contracts()

    assert len(rows) == 2
    assert {row["id"] for row in rows} == {"C001", "C002"}
    assert rows[0]["dt"] == RUN_DATE


def test_read_silver_contracts_without_table(local_catalog: Path) -> None:
    """A missing silver table reads as an empty list, not an error."""
    assert lake.read_silver_contracts() == []


def test_read_fraud_signals_roundtrip(local_catalog: Path) -> None:
    """Reads back every row written to the gold fraud_signals table."""
    signals = [
        {"entity_type": "supplier", "entity_id": "12345678000199", "signal_type": "price", "score": 0.9, "details": "{}"},
        {"entity_type": "buyer", "entity_id": "98765432000188", "signal_type": "hhi", "score": 0.8, "details": "{}"},
    ]
    lake.write_fraud_signals(signals, run_date=RUN_DATE)

    rows = lake.read_fraud_signals()

    assert len(rows) == 2
    assert {row["entity_id"] for row in rows} == {"12345678000199", "98765432000188"}
    assert rows[0]["dt"] == RUN_DATE


def test_read_fraud_signals_without_table(local_catalog: Path) -> None:
    """A missing fraud_signals table reads as an empty list, not an error."""
    assert lake.read_fraud_signals() == []


def test_write_fraud_signals_roundtrip(local_catalog: Path) -> None:
    """Gold Iceberg table stores one row per detected signal."""
    signals = [
        {
            "entity_type": "supplier",
            "entity_id": "12345678000199",
            "signal_type": "single_bid",
            "score": 0.9,
            "details": "high non-competitive rate",
        },
        {
            "entity_type": "buyer",
            "entity_id": "26000",
            "signal_type": "concentration",
            "score": 0.8,
            "details": None,
        },
    ]

    identifier = lake.write_fraud_signals(signals, run_date=RUN_DATE)

    assert identifier == "capiba.fraud_signals"
    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_GOLD).load_table(identifier)
    rows = table.scan().to_arrow()
    assert rows.num_rows == 2
    assert rows.column("signal_type")[0].as_py() == "single_bid"
    assert rows.column("score")[0].as_py() == 0.9
    assert rows.column("dt")[0].as_py() == RUN_DATE


def test_write_fraud_signals_empty_creates_table(local_catalog: Path) -> None:
    """An empty signal list creates the table without appending rows."""
    identifier = lake.write_fraud_signals([], run_date=RUN_DATE)

    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_GOLD).load_table(identifier)
    assert table.scan().to_arrow().num_rows == 0


def _company_row(cnpj_basico: str = "12345678") -> dict[str, Any]:
    """Builds a minimal Company-valid serializable record."""
    return {
        "cnpj_basico": cnpj_basico,
        "razao_social": "EMPRESA TESTE LTDA",
        "natureza_juridica": "2062",
        "qualificacao_responsavel": "49",
        "capital_social": "1000.50",
        "porte_empresa": "05",
        "ente_federativo": "PE",
    }


def test_write_silver_entities_roundtrip_typed(local_catalog: Path) -> None:
    """Silver entity tables store typed rows partitioned by dt."""
    identifier = lake.write_silver_entities(
        "companies", [_company_row()], run_date=RUN_DATE
    )

    assert identifier == "capiba.companies"
    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(identifier)
    rows = table.scan().to_arrow()
    assert rows.num_rows == 1
    assert rows.column("cnpj_basico")[0].as_py() == "12345678"
    assert rows.column("capital_social")[0].as_py() == Decimal("1000.50")
    assert rows.column("dt")[0].as_py() == RUN_DATE
    spec = table.spec()
    assert [f.name for f in spec.fields] == ["dt"]


def test_write_silver_entities_establishments_and_partners(
    local_catalog: Path,
) -> None:
    """Establishment/partner rows keep bool and date columns typed."""
    lake.write_silver_entities(
        "establishments",
        [
            {
                "cnpj": "12345678000195",
                "cnpj_basico": "12345678",
                "is_matriz": True,
                "data_inicio_atividade": "2015-06-01",
                "uf": "PE",
            }
        ],
        run_date=RUN_DATE,
    )
    lake.write_silver_entities(
        "partners",
        [
            {
                "partner_id": "a" * 32,
                "cnpj_basico": "12345678",
                "nome": "JOAO SILVA",
                "data_entrada": "2015-01-01",
            }
        ],
        run_date=RUN_DATE,
    )

    catalog = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER)
    establishments = catalog.load_table("capiba.establishments").scan().to_arrow()
    assert establishments.column("is_matriz")[0].as_py() is True
    assert establishments.column("data_inicio_atividade")[0].as_py() == date(2015, 6, 1)
    partners = catalog.load_table("capiba.partners").scan().to_arrow()
    assert partners.column("partner_id")[0].as_py() == "a" * 32
    assert partners.column("data_entrada")[0].as_py() == date(2015, 1, 1)


def test_write_silver_entities_appends_and_skips_invalid(local_catalog: Path) -> None:
    """Chunked appends accumulate rows; invalid rows are skipped."""
    lake.write_silver_entities("companies", [_company_row()], run_date=RUN_DATE)
    lake.write_silver_entities(
        "companies",
        [{"cnpj_basico": "broken"}, _company_row("87654321")],
        run_date=RUN_DATE,
    )

    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(
        "capiba.companies"
    )
    assert table.scan().to_arrow().num_rows == 2


def test_write_silver_entities_unknown_entity(local_catalog: Path) -> None:
    """An unknown entity name is a config error, not a silent no-op."""
    with pytest.raises(ValueError, match="Unknown silver entity"):
        lake.write_silver_entities("cnaes", [{}], run_date=RUN_DATE)


class TestDeleteSilverEntitiesPartition:
    """Tests for the idempotency delete-half of the dump normalization."""

    def test_unknown_entity(self, local_catalog: Path) -> None:
        """An unknown entity name is a config error, not a silent no-op."""
        with pytest.raises(ValueError, match="Unknown silver entity"):
            lake.delete_silver_entities_partition("cnaes", RUN_DATE)

    def test_offline_catalog_is_noop(
        self, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SQLite catalog has no Trino; the delete degrades to a no-op."""
        mock_query = MagicMock()
        monkeypatch.setattr(lake.trino, "run_query", mock_query)

        lake.delete_silver_entities_partition("companies", RUN_DATE)

        mock_query.assert_not_called()

    def test_deletes_partition_via_trino(
        self, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cluster path: existing table gets its partition day DELETEd."""
        monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", "http://lakekeeper:8181/catalog")
        mock_catalog = MagicMock()
        monkeypatch.setattr(lake, "get_catalog", lambda *_a: mock_catalog)
        mock_query = MagicMock()
        monkeypatch.setattr(lake.trino, "run_query", mock_query)

        lake.delete_silver_entities_partition("companies", RUN_DATE)

        mock_query.assert_called_once_with(
            "DELETE FROM silver.capiba.companies"
            f" WHERE dt = DATE '{RUN_DATE.isoformat()}'"
        )

    def test_missing_table_is_noop(
        self, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table that does not exist yet (first load) has nothing to delete."""
        from pyiceberg.exceptions import NoSuchTableError

        monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", "http://lakekeeper:8181/catalog")
        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = NoSuchTableError("nope")
        monkeypatch.setattr(lake, "get_catalog", lambda *_a: mock_catalog)
        mock_query = MagicMock()
        monkeypatch.setattr(lake.trino, "run_query", mock_query)

        lake.delete_silver_entities_partition("companies", RUN_DATE)

        mock_query.assert_not_called()


def test_read_silver_entities_roundtrip_in_batches(local_catalog: Path) -> None:
    """Reads back every typed row written to a silver entity table."""
    lake.write_silver_entities(
        "companies",
        [_company_row(), _company_row("87654321")],
        run_date=RUN_DATE,
    )

    batches = list(lake.read_silver_entities("companies"))

    rows = [row for batch in batches for row in batch]
    assert {row["cnpj_basico"] for row in rows} == {"12345678", "87654321"}
    assert rows[0]["dt"] == RUN_DATE


def test_read_silver_entities_without_table(local_catalog: Path) -> None:
    """A missing silver entity table reads as empty, not an error."""
    assert list(lake.read_silver_entities("partners")) == []


def _sanction_row(sanction_id: str = "ceis-1") -> dict[str, Any]:
    """Builds a minimal Sanction-valid serializable record."""
    return {
        "id": sanction_id,
        "list_name": "ceis",
        "cnpj": "12345678000190",
        "sanctioned_name": "EMPRESA SANCIONADA LTDA",
        "uf": "DF",
        "sanctioning_body": "MINISTERIO DA FAZENDA",
        "sanction_type": "Inidoneidade - Legislação de Licitações",
        "start_date": "2025-01-01",
        "fine_amount": "1234.56",
    }


def test_write_silver_entities_sanctions_roundtrip_typed(local_catalog: Path) -> None:
    """The silver sanctions table stores typed rows partitioned by dt."""
    identifier = lake.write_silver_entities(
        "sanctions", [_sanction_row()], run_date=RUN_DATE
    )

    assert identifier == "capiba.sanctions"
    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(identifier)
    rows = table.scan().to_arrow()
    assert rows.num_rows == 1
    assert rows.column("list_name")[0].as_py() == "ceis"
    assert rows.column("start_date")[0].as_py() == date(2025, 1, 1)
    assert rows.column("fine_amount")[0].as_py() == Decimal("1234.56")
    assert rows.column("dt")[0].as_py() == RUN_DATE
    assert [f.name for f in table.spec().fields] == ["dt"]


def test_write_silver_entities_sanctions_skips_invalid(local_catalog: Path) -> None:
    """Sanction rows failing the model (missing id/list_name) are skipped."""
    lake.write_silver_entities(
        "sanctions",
        [{"cnpj": "123"}, _sanction_row("cnep-9")],
        run_date=RUN_DATE,
    )

    table = lake.get_catalog(config.ICEBERG_WAREHOUSE_SILVER).load_table(
        "capiba.sanctions"
    )
    assert table.scan().to_arrow().num_rows == 1


def test_list_bronze_files_prefix(mock_client: MagicMock) -> None:
    """Bronze file listings use the <source>/files/dt=<date>/ prefix."""
    mock_client.list_objects.return_value = iter(
        [
            MagicMock(object_name="federal_revenue/files/dt=2026-01-15/Empresas0.zip"),
            MagicMock(object_name="federal_revenue/files/dt=2026-01-15/Cnaes.zip"),
        ]
    )

    keys = lake.list_bronze_files("federal_revenue", run_date=RUN_DATE)

    assert keys == [
        "federal_revenue/files/dt=2026-01-15/Empresas0.zip",
        "federal_revenue/files/dt=2026-01-15/Cnaes.zip",
    ]
    args, kwargs = mock_client.list_objects.call_args
    assert args[0] == config.LAKE_BUCKET_BRONZE
    assert kwargs["prefix"] == "federal_revenue/files/dt=2026-01-15/"


def test_read_bronze_file_roundtrip(mock_client: MagicMock) -> None:
    """Bronze file reads return the raw bytes and release the connection."""
    response = MagicMock()
    response.read.return_value = b"\x50\x4b zip-bytes"
    mock_client.get_object.return_value = response

    data = lake.read_bronze_file("federal_revenue/files/dt=2026-01-15/dump.zip")

    assert data == b"\x50\x4b zip-bytes"
    mock_client.get_object.assert_called_once_with(
        config.LAKE_BUCKET_BRONZE, "federal_revenue/files/dt=2026-01-15/dump.zip"
    )
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


class TestBronzePages:
    """Per-page crawl checkpoints under <source>/pages/dt=<date>/."""

    def test_write_bronze_page_key_layout_and_payload(self, mock_client: MagicMock) -> None:
        """Pages land under a deterministic page-NNNNN.json.gz key."""
        key = lake.write_bronze_page(
            "ceis", 3, [{"id": 1}, {"id": 2}], run_date=RUN_DATE
        )

        assert key == "ceis/pages/dt=2026-01-15/page-00003.json.gz"
        bucket, written_key, body = _put_call(mock_client)
        assert bucket == config.LAKE_BUCKET_BRONZE
        assert written_key == key
        assert json.loads(gzip.decompress(body)) == [{"id": 1}, {"id": 2}]

    def test_list_bronze_pages_maps_page_numbers(self, mock_client: MagicMock) -> None:
        """The listing maps page numbers to keys and skips foreign objects."""
        mock_client.list_objects.return_value = iter(
            [
                MagicMock(object_name="ceis/pages/dt=2026-01-15/page-00002.json.gz"),
                MagicMock(object_name="ceis/pages/dt=2026-01-15/page-00001.json.gz"),
                MagicMock(object_name="ceis/pages/dt=2026-01-15/notes.txt"),
            ]
        )

        pages = lake.list_bronze_pages("ceis", run_date=RUN_DATE)

        assert pages == {
            1: "ceis/pages/dt=2026-01-15/page-00001.json.gz",
            2: "ceis/pages/dt=2026-01-15/page-00002.json.gz",
        }
        _, kwargs = mock_client.list_objects.call_args
        assert kwargs["prefix"] == "ceis/pages/dt=2026-01-15/"

    def test_read_bronze_page_roundtrip(self, mock_client: MagicMock) -> None:
        """A written page checkpoint reads back as the same record list."""
        records = [{"id": 7, "nome": "EMPRESA LTDA"}]
        response = MagicMock()
        response.read.return_value = gzip.compress(json.dumps(records).encode())
        mock_client.get_object.return_value = response

        assert lake.read_bronze_page("ceis/pages/dt=2026-01-15/page-00001.json.gz") == records


class TestWriteSilverUpsert:
    """Upsert-by-id semantics of write_silver against the cluster catalog.

    The Iceberg table and Trino are mocked: the delete-half runs through
    ``capiba.pipeline.trino.run_query`` and must always precede the append.
    """

    @pytest.fixture
    def cluster_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[MagicMock, MagicMock]:
        """Non-SQLite catalog URI with the table and Trino mocked out."""
        table = MagicMock()
        monkeypatch.setattr(lake, "_ensure_table", lambda *args: table)
        monkeypatch.setattr(lake, "_arrow_schema", lambda _table: None)
        monkeypatch.setattr(
            lake, "ICEBERG_CATALOG_URI", "http://lakekeeper:8181/catalog"
        )
        run_query = MagicMock()
        monkeypatch.setattr(trino, "run_query", run_query)
        return table, run_query

    @staticmethod
    def _delete_ids(sql: str) -> list[str]:
        """Extracts the id literals of a DELETE ... WHERE id IN (...) call."""
        assert sql.startswith("DELETE FROM silver.capiba.contracts WHERE id IN (")
        return sql.removeprefix(
            "DELETE FROM silver.capiba.contracts WHERE id IN ("
        ).removesuffix(")").split(", ")

    def test_delete_precedes_append_with_exact_ids(
        self, cluster_catalog: tuple[MagicMock, MagicMock]
    ) -> None:
        """DELETE chunks (500 ids) run before the append, with quote escaping."""
        table, run_query = cluster_catalog

        def _append(_rows: Any) -> None:
            # At append time, every DELETE chunk must already have run.
            assert run_query.call_count == 3

        table.append.side_effect = _append

        records = [_contract(f"C{i:04d}") for i in range(1200)]
        records.append(_contract("x'y"))  # single quote must be escaped

        lake.write_silver(records, run_date=RUN_DATE)

        assert run_query.call_count == 3
        chunks = [self._delete_ids(c.args[0]) for c in run_query.call_args_list]
        assert [len(chunk) for chunk in chunks] == [500, 500, 201]
        assert chunks[0][0] == "'C0000'"
        assert chunks[2][-1] == "'x''y'"
        table.append.assert_called_once()

    def test_delete_failure_propagates_without_append(
        self, cluster_catalog: tuple[MagicMock, MagicMock]
    ) -> None:
        """A failed DELETE aborts the write: no append, no duplicates."""
        table, run_query = cluster_catalog
        run_query.side_effect = RuntimeError("trino down")

        with pytest.raises(RuntimeError, match="trino down"):
            lake.write_silver([_contract("C001")], run_date=RUN_DATE)

        table.append.assert_not_called()

    def test_append_failure_propagates_after_delete(
        self, cluster_catalog: tuple[MagicMock, MagicMock]
    ) -> None:
        """A failed append after the DELETE propagates (a re-run restores)."""
        table, run_query = cluster_catalog
        table.append.side_effect = RuntimeError("s3 down")

        with pytest.raises(RuntimeError, match="s3 down"):
            lake.write_silver([_contract("C001")], run_date=RUN_DATE)

        run_query.assert_called_once()

    def test_sqlite_catalog_keeps_pure_append(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The offline SQLite catalog has no Trino: append without DELETE."""
        table = MagicMock()
        monkeypatch.setattr(lake, "_ensure_table", lambda *args: table)
        monkeypatch.setattr(lake, "_arrow_schema", lambda _table: None)
        monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", "sqlite:///catalog.db")
        run_query = MagicMock()
        monkeypatch.setattr(trino, "run_query", run_query)

        lake.write_silver([_contract("C001")], run_date=RUN_DATE)

        run_query.assert_not_called()
        table.append.assert_called_once()

    def test_no_valid_rows_skips_delete_and_append(
        self, cluster_catalog: tuple[MagicMock, MagicMock]
    ) -> None:
        """An all-invalid batch writes nothing and issues no DELETE."""
        table, run_query = cluster_catalog

        lake.write_silver([{"id": "broken"}], run_date=RUN_DATE)

        run_query.assert_not_called()
        table.append.assert_not_called()
