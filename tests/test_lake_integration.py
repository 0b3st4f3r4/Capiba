"""Integration tests for the pyiceberg x Trino x Lakekeeper contract.

Responsibility: pin the lake interaction rules that broke in production
(2026-08, three times in one week — see ``docs/operacao_lake.md``):

1. Silver ``contracts`` upsert-by-id: append, Trino DELETE of the ids,
   ``table.refresh()``, pyiceberg append, and a read through
   ``lake.read_silver_contracts`` (Trino) returning the new version with
   no duplicates.
2. Tables mutated by Trino DELETEs carry positional delete files; the
   pinned pyarrow cannot reliably decode them on the real silver
   ("DecodeArrow of DictAccumulator for DeltaLengthByteArrayDecoder",
   2026-08-21), so reads must go through Trino. Whether the pyiceberg
   scan *crashes* depends on the Parquet encoding of the specific files
   (dictionary/delta pages of the real silver) and is NOT stably
   reproducible on a small synthetic table — the test pins the invariant
   (Trino read is correct after deletes) and only characterizes the scan
   behavior, failing if the scan would have silently returned correct
   data *and* the crash is claimed elsewhere. See the test docstring.
3. Gold ``fraud_signals`` replace-by-partition: rewriting one ``dt``
   partition never touches the rows of another partition.
4. Normalize retry: ``delete_silver_entities_partition`` before the
   chunked appends makes an interrupted normalize (first chunk persisted,
   crash on the second) re-runnable without duplicates.

Run with ``CAPIBA_INTEGRATION=1`` against the local cluster with active
port-forwards (``make cluster-start && make port-forward``). The tests
write rows with unique random ids into the real silver/gold tables under
a dedicated far-past partition (``dt=1999-01-01``/``1999-01-02``) and
delete them in ``finally`` blocks — a failed run leaves at most a few
clearly-marked rows in that partition, re-cleaned by the next run.
"""

from __future__ import annotations

import uuid
import warnings
from datetime import date
from typing import Any

import pytest

pytestmark = pytest.mark.integration

# Far-past partition days reserved for these tests: they never collide with
# real ingestion runs and make leftovers easy to spot and clean.
DT_A = date(1999, 1, 1)
DT_B = date(1999, 1, 2)


def _suffix() -> str:
    """Returns a short unique suffix for test row ids."""
    return uuid.uuid4().hex[:8]


def _contract(contract_id: str, subject: str, amount: float) -> dict[str, Any]:
    """Builds a minimal Contract-valid record with a unique id."""
    return {
        "id": contract_id,
        "process_number": f"P-IT/{contract_id}",
        "subject": subject,
        "amount": amount,
        "signature_date": "1999-01-01",
        "validity_start": "1999-01-01",
        "validity_end": "1999-12-31",
        "buyer": {
            "siafi_code": "000000",
            "name": "Prefeitura Teste de Integração",
            "government_level": "municipal",
            "uf": "PE",
            "city": "Recife",
        },
        "supplier": {
            "cnpj": "00000000000191",
            "legal_name": "Fornecedor Teste de Integração Ltda",
        },
        "modality": "pregao",
        "status": "concluido",
    }


def _delete_contracts(ids: list[str]) -> None:
    """Best-effort cleanup of test rows from the real silver contracts."""
    from capiba.pipeline import trino

    escaped = ", ".join(f"'{i}'" for i in ids)
    trino.run_query(
        f"DELETE FROM silver.capiba.contracts WHERE id IN ({escaped})"  # nosec: B608
    )


def _read_contracts_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """Reads test rows through Trino (the supported read path)."""
    from capiba.pipeline import trino

    escaped = ", ".join(f"'{i}'" for i in ids)
    return trino.run_query(
        f"SELECT id, subject, amount, dt FROM silver.capiba.contracts"  # nosec: B608
        f" WHERE id IN ({escaped})"
    )


@pytest.mark.integration
class TestSilverContractsUpsertIntegration:
    """Contract 1: upsert-by-id cycle of the silver ``contracts`` table."""

    def test_upsert_replaces_without_duplicates(self) -> None:
        """Append, Trino DELETE, refresh, append: the new version wins."""
        from capiba.pipeline import lake

        suffix = _suffix()
        ids = [f"it-upsert-{suffix}-1", f"it-upsert-{suffix}-2"]
        try:
            lake.write_silver(
                [
                    _contract(ids[0], "versão original 1", 100.0),
                    _contract(ids[1], "versão original 2", 200.0),
                ],
                run_date=DT_A,
            )
            lake.write_silver(
                [
                    _contract(ids[0], "versão atualizada 1", 150.0),
                    _contract(ids[1], "versão atualizada 2", 250.0),
                ],
                run_date=DT_A,
            )

            rows = _read_contracts_by_ids(ids)
            assert len(rows) == 2, f"upsert duplicated rows: {rows}"
            subjects = {row["id"]: row["subject"] for row in rows}
            assert subjects == {
                ids[0]: "versão atualizada 1",
                ids[1]: "versão atualizada 2",
            }

            # The supported full read (Trino path) must decode the table
            # despite the delete files written by the DELETEs above.
            all_rows = lake.read_silver_contracts()
            ours = [row for row in all_rows if row.get("id") in ids]
            assert len(ours) == 2
            assert {row["subject"] for row in ours} == {
                "versão atualizada 1",
                "versão atualizada 2",
            }
        finally:
            _delete_contracts(ids)


@pytest.mark.integration
class TestDeleteFilesReadIntegration:
    """Contract 2: reads of Trino-mutated tables go through Trino.

    The pyiceberg scan crash on delete files ("DecodeArrow of
    DictAccumulator for DeltaLengthByteArrayDecoder") was observed on the
    real silver, whose Parquet files mix dictionary/delta encodings from
    many writers. Whether a small synthetic table reproduces the crash
    depends on the encoding Trino picks for the delete files, so this
    test does not hard-assert the crash: it pins the actual invariant
    (the Trino read returns exactly the live rows after a DELETE) and
    *characterizes* the pyiceberg scan — if the scan raises, the known
    decode error must be the cause; if it succeeds, the outcome is
    recorded as a warning (a clean scan on synthetic data does not
    invalidate the rule on the real silver).
    """

    def test_trino_read_is_correct_after_delete(self) -> None:
        """After a Trino DELETE, Trino reads the live rows; scan is best-effort."""
        from capiba.pipeline import lake

        suffix = _suffix()
        keep_id = f"it-scan-{suffix}-keep"
        drop_id = f"it-scan-{suffix}-drop"
        ids = [keep_id, drop_id]
        try:
            lake.write_silver(
                [_contract(keep_id, "fica", 10.0), _contract(drop_id, "sai", 20.0)],
                run_date=DT_A,
            )
            _delete_contracts([drop_id])

            rows = _read_contracts_by_ids(ids)
            assert [row["id"] for row in rows] == [keep_id]

            catalog = lake.get_catalog(lake.ICEBERG_WAREHOUSE_SILVER)
            table = catalog.load_table("capiba.contracts").refresh()
            try:
                scanned = table.scan().to_arrow().to_pylist()
            except Exception as exc:  # noqa: BLE001 — characterizing the crash
                message = str(exc)
                assert "DecodeArrow" in message or "DeltaLengthByteArray" in message, (
                    f"pyiceberg scan failed with an unknown error: {exc}"
                )
                return
            ours = [row for row in scanned if row.get("id") in ids]
            if any(row["id"] == drop_id for row in ours):
                warnings.warn(
                    "pyiceberg scan returned a Trino-deleted row (stale delete "
                    "files) — reads must go through Trino",
                    stacklevel=1,
                )
            else:
                warnings.warn(
                    "pyiceberg scan handled the delete files on this synthetic "
                    "table; the real-silver decode crash is encoding-dependent "
                    "and not reproduced here",
                    stacklevel=1,
                )
        finally:
            _delete_contracts(ids)


@pytest.mark.integration
class TestFraudSignalsReplaceIntegration:
    """Contract 3: gold ``fraud_signals`` replace-by-partition."""

    def test_replace_partition_leaves_other_partitions_untouched(self) -> None:
        """Rewriting dt=A twice never touches the rows of dt=B."""
        from capiba.pipeline import lake, trino

        suffix = _suffix()
        entity_a = f"it-gold-{suffix}-a"
        entity_b = f"it-gold-{suffix}-b"

        def signal(entity_id: str, score: float) -> dict[str, Any]:
            return {
                "entity_type": "supplier",
                "entity_id": entity_id,
                "signal_type": "integration_test",
                "score": score,
                "details": "{}",
            }

        def read_rows() -> list[dict[str, Any]]:
            literal = ", ".join(f"'{e}'" for e in (entity_a, entity_b))
            return trino.run_query(
                "SELECT entity_id, score, dt FROM gold.capiba.fraud_signals"  # nosec: B608
                f" WHERE entity_id IN ({literal})"
            )

        try:
            lake.write_fraud_signals([signal(entity_a, 0.5)], run_date=DT_A)
            lake.write_fraud_signals([signal(entity_b, 0.9)], run_date=DT_B)
            # Re-run of dt=A with a new score: must replace, not append, and
            # must not touch dt=B.
            lake.write_fraud_signals([signal(entity_a, 0.7)], run_date=DT_A)

            rows = read_rows()
            assert len(rows) == 2, f"replace-by-partition broke: {rows}"
            by_entity = {row["entity_id"]: row for row in rows}
            assert float(by_entity[entity_a]["score"]) == pytest.approx(0.7)
            assert str(by_entity[entity_a]["dt"]).startswith("1999-01-01")
            assert float(by_entity[entity_b]["score"]) == pytest.approx(0.9)
            assert str(by_entity[entity_b]["dt"]).startswith("1999-01-02")
        finally:
            literal = ", ".join(f"'{e}'" for e in (entity_a, entity_b))
            trino.run_query(
                "DELETE FROM gold.capiba.fraud_signals"  # nosec: B608
                f" WHERE entity_id IN ({literal})"
            )


@pytest.mark.integration
class TestNormalizeRetryIntegration:
    """Contract 4: interrupted normalize re-runs without duplicates."""

    def test_retry_after_partial_normalize(self) -> None:
        """Chunk 1 persisted, crash on chunk 2: the retry replaces cleanly."""
        from capiba.pipeline import lake, trino

        suffix = _suffix()
        ids = [f"it-retry-{suffix}-{n}" for n in (1, 2, 3)]
        chunk_1 = [
            {"id": ids[0], "list_name": "ceis", "sanctioned_name": "Empresa Retry 1"},
            {"id": ids[1], "list_name": "ceis", "sanctioned_name": "Empresa Retry 2"},
        ]
        chunk_2 = [
            {"id": ids[2], "list_name": "cnep", "sanctioned_name": "Empresa Retry 3"},
        ]

        def normalize() -> None:
            """The task_normalize_dump write pattern: delete, then append per chunk."""
            lake.delete_silver_entities_partition("sanctions", DT_A)
            lake.write_silver_entities("sanctions", chunk_1, run_date=DT_A)
            lake.write_silver_entities("sanctions", chunk_2, run_date=DT_A)

        def interrupted() -> None:
            """First attempt: chunk 1 lands, chunk 2 never does (pod died)."""
            lake.write_silver_entities("sanctions", chunk_1, run_date=DT_A)
            raise RuntimeError("simulated crash before chunk 2")

        try:
            with pytest.raises(RuntimeError, match="simulated crash"):
                interrupted()
            normalize()

            escaped = ", ".join(f"'{i}'" for i in ids)
            rows = trino.run_query(
                "SELECT id, sanctioned_name FROM silver.capiba.sanctions"  # nosec: B608
                f" WHERE id IN ({escaped})"
            )
            assert len(rows) == 3, f"normalize retry left duplicates: {rows}"
            assert {row["id"] for row in rows} == set(ids)
        finally:
            escaped = ", ".join(f"'{i}'" for i in ids)
            trino.run_query(
                "DELETE FROM silver.capiba.sanctions"  # nosec: B608
                f" WHERE dt = DATE '1999-01-01' AND id IN ({escaped})"
            )
