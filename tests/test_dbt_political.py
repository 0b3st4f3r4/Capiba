"""Offline checks for the political_connections mart (PR-D-08 slice 3).

Responsibility: validate the mart SQL and the UE<->SIAFI seed without any
infra — the rendered model and singular tests must parse as Trino SQL
(sqlglot), the LGPD masking must be present, and the seed must carry the
verified pilot row (Recife: TSE UE 25313, SIAFI 2531, IBGE 2611606).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import sqlglot

from capiba.config import DBT_PROJECT_DIR

MART = Path(DBT_PROJECT_DIR) / "models" / "marts" / "political_connections.sql"
TESTS_DIR = Path(DBT_PROJECT_DIR) / "tests"
SEED = Path(DBT_PROJECT_DIR) / "seeds" / "ue_siafi_crosswalk.csv"

_RELATIONS = {
    ("lake_gold", "fraud_signals"): "gold.capiba.fraud_signals",
    ("lake_silver", "candidacies"): "silver.capiba.candidacies",
    ("lake_silver", "campaign_donations"): "silver.capiba.campaign_donations",
}


def _render(path: Path) -> str:
    """Renders the dbt Jinja of a model/test into plain SQL for parsing."""
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"\{\{ config\(.*?\) \}\}", "", sql)

    def _source(match: re.Match[str]) -> str:
        key = (match.group(1), match.group(2))
        return _RELATIONS.get(key, f"{key[0]}.capiba.{key[1]}")

    sql = re.sub(r"\{\{ source\('(\w+)', '(\w+)'\) \}\}", _source, sql)
    sql = re.sub(r"\{\{ ref\('(\w+)'\) \}\}", r"gold.capiba.\1", sql)
    return sql


class TestMartSql:
    def test_mart_parses_as_trino_sql(self) -> None:
        statements = sqlglot.parse(_render(MART), read="trino")
        assert len(statements) == 1

    def test_singular_tests_parse_as_trino_sql(self) -> None:
        for name in (
            "political_connections_no_full_cpf.sql",
            "political_connections_gates.sql",
        ):
            statements = sqlglot.parse(_render(TESTS_DIR / name), read="trino")
            assert len(statements) == 1

    def test_full_document_never_leaves_the_mart(self) -> None:
        """The final projection exposes a hash key and a masked document."""
        sql = _render(MART)
        final_select = sql[sql.rindex("\nselect\n"): sql.rindex("from enriched")]
        # No bare document column: every mention must be inside the sha256
        # concat or the masked-document case.
        for line in final_select.splitlines():
            assert not re.match(r"^\s*(p\.)?donor_document\s*,?\s*$", line)
        assert "sha256" in final_select  # signal_id
        assert "donor_document_masked" in final_select
        assert "'***' || substr(donor_document, 4, 6) || '**'" in sql

    def test_mart_uses_latest_snapshot_partitions(self) -> None:
        """The silver TSE tables are monthly snapshots; only max(dt) counts."""
        sql = _render(MART)
        assert sql.count("max(dt)") == 2
        assert "signal_type = 'political_connection'" in sql


class TestUeSiafiCrosswalkSeed:
    def test_pilot_recife_row(self) -> None:
        rows = list(csv.DictReader(SEED.open(encoding="utf-8")))
        assert rows == [
            {
                "ue_code": "25313",
                "municipality": "RECIFE",
                "uf": "PE",
                "siafi_code": "2531",
                "ibge_code": "2611606",
            }
        ]

    def test_ue_code_is_unique(self) -> None:
        rows = list(csv.DictReader(SEED.open(encoding="utf-8")))
        ue_codes = [r["ue_code"] for r in rows]
        assert len(ue_codes) == len(set(ue_codes))
