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
from capiba.pipeline import lake

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
