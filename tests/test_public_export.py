"""Tests for the public gold marts batch export (O11).

Responsibility: Validate the fail-closed LGPD classification (every dbt
mart must be classified to be exportable), the CSV/Parquet serializers,
the export flow with faked Trino/MinIO and the Airflow task wrapper.
"""

from __future__ import annotations

import csv
import io
import json
import unittest.mock as mock
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from capiba.config import DBT_PROJECT_DIR, PUBLIC_EXPORT_BUCKET
from capiba.pipeline import public_export
from capiba.pipeline.public_export import (
    EXCLUDED_MARTS,
    PUBLIC_MARTS,
    export_mart,
    export_object_key,
    export_public_marts,
    rows_to_csv,
    rows_to_parquet,
    task_export_public_marts,
)

_ROWS = [
    {"siafi_code": "123456", "contracts": 3, "total_amount": 1500.5},
    {"siafi_code": "654321", "contracts": 1, "total_amount": 200.0},
]


class _FakeMinio:
    """In-memory MinIO stand-in (captures put_object writes)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(
        self,
        bucket: str,
        key: str,
        stream: io.BytesIO,
        length: int,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.objects[(bucket, key)] = stream.read()


def _fake_run_query(sql: str) -> list[dict[str, Any]]:
    return list(_ROWS)


class TestClassification:
    """The LGPD allowlist is fail-closed over the dbt marts."""

    def _dbt_marts(self) -> set[str]:
        marts_dir = Path(DBT_PROJECT_DIR) / "models" / "marts"
        return {p.stem for p in marts_dir.glob("*.sql")}

    def test_every_dbt_mart_is_classified(self) -> None:
        """A new mart without classification fails here (never exported)."""
        unclassified = self._dbt_marts() - set(PUBLIC_MARTS) - set(EXCLUDED_MARTS)
        assert unclassified == set()

    def test_no_stale_classification_entries(self) -> None:
        """A classification for a mart that no longer exists fails here."""
        stale = (set(PUBLIC_MARTS) | set(EXCLUDED_MARTS)) - self._dbt_marts()
        assert stale == set()

    def test_classification_sets_are_disjoint(self) -> None:
        assert set(PUBLIC_MARTS) & set(EXCLUDED_MARTS) == set()

    def test_classify_marts_blocks_unknown(self) -> None:
        exportable, blocked = public_export.classify_marts(
            ["contracts_daily", "mart_novo_sem_classificacao", "pod_usage_hourly"]
        )
        assert exportable == ["contracts_daily"]
        assert blocked == ["mart_novo_sem_classificacao"]


class TestSerializers:
    def test_csv_roundtrip(self) -> None:
        payload = rows_to_csv(_ROWS)
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
        assert len(rows) == 2
        assert rows[0]["siafi_code"] == "123456"
        assert rows[1]["total_amount"] == "200.0"

    def test_csv_empty(self) -> None:
        assert rows_to_csv([]) == b""

    def test_parquet_roundtrip(self) -> None:
        payload = rows_to_parquet(_ROWS)
        table = pq.read_table(io.BytesIO(payload))
        assert table.num_rows == 2
        assert table.column_names == ["siafi_code", "contracts", "total_amount"]

    def test_parquet_empty(self) -> None:
        assert rows_to_parquet([]) == b""


class TestExport:
    def test_export_object_key_convention(self) -> None:
        key = export_object_key("contracts_daily", date(2026, 8, 20), "manifest.json")
        assert key == "marts/contracts_daily/dt=2026-08-20/manifest.json"

    def test_export_object_key_rejects_invalid_names(self) -> None:
        with pytest.raises(ValueError, match="invalid mart name"):
            export_object_key("bad;DROP", date(2026, 8, 20), "x.csv")

    def test_export_mart_writes_csv_parquet_and_manifest(self) -> None:
        client = _FakeMinio()
        summary = export_mart(
            "contracts_daily", date(2026, 8, 20), run_query=_fake_run_query, client=client
        )

        assert summary["rows"] == 2
        prefix = "marts/contracts_daily/dt=2026-08-20"
        assert (PUBLIC_EXPORT_BUCKET, f"{prefix}/contracts_daily.csv") in client.objects
        assert (PUBLIC_EXPORT_BUCKET, f"{prefix}/contracts_daily.parquet") in client.objects
        manifest = json.loads(
            client.objects[(PUBLIC_EXPORT_BUCKET, f"{prefix}/manifest.json")]
        )
        assert manifest["mart"] == "contracts_daily"
        assert manifest["rows"] == 2
        assert manifest["lgpd_classification"] == PUBLIC_MARTS["contracts_daily"]
        csv_sha = summary["files"]["csv"]["sha256"]
        assert csv_sha == manifest["files"]["csv"]["sha256"]

    def test_export_mart_refuses_non_allowlisted(self) -> None:
        with pytest.raises(ValueError, match="allowlist"):
            export_mart(
                "data_quality_daily",
                date(2026, 8, 20),
                run_query=_fake_run_query,
                client=_FakeMinio(),
            )

    def test_export_public_marts_covers_the_allowlist(self) -> None:
        client = _FakeMinio()
        summary = export_public_marts(
            date(2026, 8, 20),
            run_query=_fake_run_query,
            client=client,
            list_tables=lambda: [f"capiba.{m}" for m in PUBLIC_MARTS],
        )

        assert summary["marts"] == len(PUBLIC_MARTS)
        exported = {e["mart"] for e in summary["exports"]}
        assert exported == set(PUBLIC_MARTS)
        assert "pod_usage_hourly" not in exported

    def test_export_public_marts_skips_marts_without_gold_table(self) -> None:
        """A mart excluded from the dbt run (source silvers missing, e.g.
        TSE) has no gold table; the export skips it with a warning instead
        of failing the whole batch."""
        client = _FakeMinio()
        present = [m for m in PUBLIC_MARTS if m != "political_connections"]
        summary = export_public_marts(
            date(2026, 8, 20),
            run_query=_fake_run_query,
            client=client,
            list_tables=lambda: [f"capiba.{m}" for m in present],
        )

        assert summary["marts"] == len(present)
        assert summary["skipped"] == ["political_connections"]
        exported = {e["mart"] for e in summary["exports"]}
        assert "political_connections" not in exported

    def test_task_uses_the_airflow_run_date(self) -> None:
        client = _FakeMinio()
        with (
            mock.patch.object(public_export.trino, "run_query", _fake_run_query),
            mock.patch.object(public_export.lake, "get_client", lambda: client),
            mock.patch.object(
                public_export.trino,
                "list_iceberg_tables",
                lambda catalog: [f"capiba.{m}" for m in PUBLIC_MARTS],
            ),
        ):
            summary = task_export_public_marts(ds="2026-08-19")
        assert summary["run_date"] == "2026-08-19"
        assert summary["marts"] == len(PUBLIC_MARTS)

    def test_export_mart_rejects_allowlisted_name_failing_the_regex(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The identifier regex is a second guard after the allowlist."""
        monkeypatch.setitem(public_export.PUBLIC_MARTS, "bad;name", "test")
        with pytest.raises(ValueError, match="invalid mart name"):
            export_mart(
                "bad;name",
                date(2026, 8, 20),
                run_query=_fake_run_query,
                client=_FakeMinio(),
            )

    def test_lake_ds_without_ds_is_none(self) -> None:
        """A context without ``ds`` resolves to None (today's partition)."""
        assert public_export._lake_ds({}) is None
        assert public_export._lake_ds({"ds": ""}) is None
        assert public_export._lake_ds({"ds": "2026-08-19"}) == date(2026, 8, 19)
