"""Tests for the pipeline vertical slice.

Responsibility: Validate atomic tasks and DAGs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from capiba.config import DETECTION_ENTITY_THRESHOLD
from capiba.pipeline.tasks import (
    _lake_run_date,
    persist_cnpj_entities,
    persist_contracts,
    task_dbt_run,
    task_detect,
    task_post_step,
)


def _silver_contract(
    contract_id: str,
    buyer_siafi: str = "123456",
    supplier_cnpj: str = "12345678000195",
    amount: float = 1000.0,
    validity_days: int = 30,
) -> dict[str, Any]:
    """Builds a silver-shaped contract row for the detection tests."""
    return {
        "id": contract_id,
        "amount": amount,
        "validity_start": "2026-01-01",
        "validity_end": f"2026-01-{1 + validity_days:02d}",
        "buyer": {"siafi_code": buyer_siafi},
        "supplier": {"cnpj": supplier_cnpj},
    }


class TestLakeRunDate:
    """Tests for the Airflow context run-date extraction."""

    def test_logical_date_datetime(self) -> None:
        """A datetime ``logical_date`` must be converted to a date."""
        context = {"logical_date": datetime(2026, 3, 5, 12, 30)}

        assert _lake_run_date(context) == date(2026, 3, 5)

    def test_dag_run_run_after_string(self) -> None:
        """A string ``run_after`` on the DAG run must be parsed as a date."""
        dag_run = MagicMock()
        dag_run.run_after = "2026-03-05T00:00:00+00:00"

        assert _lake_run_date({"dag_run": dag_run}) == date(2026, 3, 5)

    def test_dag_run_run_after_datetime(self) -> None:
        """A datetime ``run_after`` on the DAG run must be converted to a date."""
        dag_run = MagicMock()
        dag_run.run_after = datetime(2026, 3, 5, 0, 0)

        assert _lake_run_date({"dag_run": dag_run}) == date(2026, 3, 5)

    def test_empty_context(self) -> None:
        """Without any date key the result must be None."""
        assert _lake_run_date({}) is None


class TestPersistContracts:
    """Tests for the pure persistence step."""

    @patch("capiba.pipeline.tasks.bulk_upsert_contracts")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_persist_success(
        self,
        mock_get_db: MagicMock,
        mock_bulk: MagicMock,
        sample_contracts: list[dict[str, object]],
    ) -> None:
        """Valid contracts must be revalidated, persisted and traced."""
        mock_bulk.return_value = {"inserted": 2, "updated": 0}

        summary = persist_contracts(sample_contracts, execution_date="2026-01-01")

        assert summary["inserted"] == 2
        assert summary["source_id"]
        assert "lineage" in summary
        db, contracts = mock_bulk.call_args.args[:2]
        assert db is mock_get_db.return_value
        assert len(contracts) == 2

    @patch("capiba.pipeline.tasks.bulk_upsert_contracts")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_persist_skips_invalid_contracts(
        self,
        mock_get_db: MagicMock,
        mock_bulk: MagicMock,
        sample_contracts: list[dict[str, object]],
    ) -> None:
        """Contracts that fail revalidation must be skipped."""
        mock_bulk.return_value = {"inserted": 1, "updated": 0}

        summary = persist_contracts([*sample_contracts, {"id": "broken"}])

        assert summary["inserted"] == 1
        contracts = mock_bulk.call_args.args[1]
        assert len(contracts) == 2

    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_persist_failure(self, mock_get_db: MagicMock) -> None:
        """Database failures must be reported as an error summary."""
        mock_get_db.side_effect = ConnectionError("arango down")

        summary = persist_contracts([{"id": "C001"}])

        assert summary == {"error": "arango down"}


class TestPersistCnpjEntities:
    """Entity resolution runs best-effort after the CNPJ graph load (O5)."""

    @patch("capiba.pipeline.tasks.resolve_entities")
    @patch("capiba.pipeline.tasks.bulk_upsert_cnpj")
    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_resolution_summary_after_load(
        self,
        mock_get_db: MagicMock,
        mock_lake: MagicMock,
        mock_upsert: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """same_as edges are resolved over the loaded persons, on the
        pre-registered threshold."""
        mock_lake.read_silver_entities.side_effect = lambda entity: iter([[]])
        mock_upsert.return_value = {
            "companies": 1,
            "persons": 2,
            "edges": 2,
            "errors": 0,
        }
        mock_resolve.return_value = {
            "persons": 2,
            "candidate_pairs": 1,
            "same_as": 1,
            "threshold": 0.85,
        }

        summary = persist_cnpj_entities(execution_date="2026-08-20")

        assert summary["same_as"] == 1
        assert "error" not in summary
        mock_resolve.assert_called_once_with(
            mock_get_db.return_value, threshold=DETECTION_ENTITY_THRESHOLD
        )

    @patch("capiba.pipeline.tasks.resolve_entities")
    @patch("capiba.pipeline.tasks.bulk_upsert_cnpj")
    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_resolution_failure_never_fails_the_load(
        self,
        mock_get_db: MagicMock,
        mock_lake: MagicMock,
        mock_upsert: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        """A resolution error is reported apart; the graph load stands."""
        mock_lake.read_silver_entities.side_effect = lambda entity: iter([[]])
        mock_upsert.return_value = {
            "companies": 1,
            "persons": 0,
            "edges": 0,
            "errors": 0,
        }
        mock_resolve.side_effect = RuntimeError("aql boom")

        summary = persist_cnpj_entities(execution_date="2026-08-20")

        assert summary["companies"] == 2  # one batch per entity table
        assert summary["resolution_error"] == "aql boom"
        assert "error" not in summary

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_load_failure_skips_resolution(
        self, mock_get_db: MagicMock, mock_lake: MagicMock
    ) -> None:
        """A failed load reports the error and never reaches resolution."""
        mock_lake.read_silver_entities.side_effect = ConnectionError("lake down")

        summary = persist_cnpj_entities(execution_date="2026-08-20")

        assert summary["error"] == "lake down"


class TestTaskDetect:
    """Tests for the fraud-signal detection task."""

    @pytest.fixture(autouse=True)
    def _mock_evidence(self) -> Any:
        """Evidence storage (MinIO) is mocked in every detect test."""
        with (
            patch("capiba.pipeline.tasks.EvidenceStorage"),
            patch("capiba.pipeline.tasks.store_signal_packages") as mock_store,
        ):
            self.mock_store_packages = mock_store
            yield

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_writes_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Signals computed from the silver table must be written to gold."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert mock_lake.write_fraud_signals.call_args.kwargs["run_date"] == date(
            2026, 1, 1
        )
        assert {s["signal_type"] for s in signals} == {"concentration"}

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_adds_collusion_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Collusion pairs from the graph must be aggregated as signals."""
        from capiba.config import DETECTION_COLLUSION_MIN_WINS

        mock_lake.read_silver_contracts.return_value = []
        mock_collusion.return_value = [
            {"buyer": "B1", "supplier": "91000000000002", "wins": 4},
            {"buyer": "B1", "supplier": "91000000000001", "wins": 4},
        ]

        summary = task_detect(ds="2026-01-01")

        mock_collusion.assert_called_once_with(
            mock_get_db.return_value, min_wins=DETECTION_COLLUSION_MIN_WINS
        )
        assert summary["signals"] == 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert signals[0]["signal_type"] == "collusion_network"
        assert signals[0]["entity_id"] == "91000000000001+91000000000002"

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_adds_sanctioned_supplier_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Sanction screening (O3): suppliers under a vigent sanction signal."""
        contract = _silver_contract("C001", supplier_cnpj="11111111000111")
        contract["signature_date"] = "2026-06-15"
        mock_lake.read_silver_contracts.return_value = [contract]
        mock_lake.read_silver_entities.return_value = iter(
            [
                [
                    {
                        "id": "ceis-1",
                        "list_name": "ceis",
                        "cnpj": "11111111000111",
                        "cpf": None,
                        "start_date": "2026-01-01",
                        "end_date": None,
                    }
                ]
            ]
        )
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        mock_lake.read_silver_entities.assert_any_call("sanctions")
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        screened = [s for s in signals if s["signal_type"] == "sanctioned_supplier"]
        assert len(screened) == 1
        assert screened[0]["entity_id"] == "11111111000111"
        assert screened[0]["score"] == 1.0
        assert summary["signals"] == len(signals)

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_adds_sanctioned_name_match_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Fuzzy screening (PR-D-06b): masked-document name matches signal."""
        contract = _silver_contract("C001", supplier_cnpj="")
        contract["signature_date"] = "2026-06-15"
        contract["supplier"] = {
            "legal_name": "MARIA DE FATIMA PEREIRA",
            "cpf": "12343515100",
        }
        mock_lake.read_silver_contracts.return_value = [contract]
        mock_lake.read_silver_entities.return_value = iter(
            [
                [
                    {
                        "id": "ceaf-1",
                        "list_name": "ceaf",
                        "cnpj": None,
                        "cpf": None,
                        "masked_document": "***435151**",
                        "sanctioned_name": "MARIA DE FATIMA PEREIRA",
                        "start_date": "2026-01-01",
                        "end_date": None,
                    }
                ]
            ]
        )
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        signals = mock_lake.write_fraud_signals.call_args.args[0]
        fuzzy = [s for s in signals if s["signal_type"] == "sanctioned_name_match"]
        assert len(fuzzy) == 1
        assert fuzzy[0]["entity_id"] == "12343515100"
        assert fuzzy[0]["score"] == 1.0
        assert summary["signals"] == len(signals)

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_sanctions_failure_keeps_other_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """A missing silver sanctions table must not abort the detection."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_lake.read_silver_entities.side_effect = FileNotFoundError("no table")
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert "sanctioned_supplier" not in {s["signal_type"] for s in signals}

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_adds_political_connection_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Political screening (O8, PR-D-08): donor of an elected mayor who
        supplies the municipality signals via the silver TSE tables."""
        contract = _silver_contract("C001", supplier_cnpj="11111111000111")
        contract["signature_date"] = "2026-06-15"
        contract["buyer"] = {"siafi_code": "2650", "city": "RECIFE", "uf": "PE"}
        other = _silver_contract("C002", supplier_cnpj="22222222000122")
        other["signature_date"] = "2026-06-15"
        other["buyer"] = {"siafi_code": "2650", "city": "RECIFE", "uf": "PE"}
        other["amount"] = 3000.0
        mock_lake.read_silver_contracts.return_value = [contract, other]
        batches = {
            "sanctions": [],
            "campaign_donations": [
                [
                    {
                        "election_year": 2024,
                        "candidate_sequential": "9001",
                        "donor_document": "11111111000111",
                        "donor_origin_document": None,
                        "amount": 5000.0,
                    }
                ]
            ],
            "candidacies": [
                [
                    {
                        "election_year": 2024,
                        "candidate_sequential": "9001",
                        "candidate_name": "JOANA CANDIDATA",
                        "party": "XX",
                        "office": "Prefeito",
                        "ue_name": "RECIFE",
                        "uf": "PE",
                        "totalization_status": "Eleito",
                    }
                ]
            ],
        }
        mock_lake.read_silver_entities.side_effect = lambda entity: iter(
            batches[entity]
        )
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        mock_lake.read_silver_entities.assert_any_call("campaign_donations")
        mock_lake.read_silver_entities.assert_any_call("candidacies")
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        political = [s for s in signals if s["signal_type"] == "political_connection"]
        assert len(political) == 1
        assert political[0]["entity_type"] == "supplier"
        assert political[0]["entity_id"] == "11111111000111"
        assert political[0]["score"] == 1.0  # share 0.25 saturates at the cap
        assert summary["signals"] == len(signals)

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_tse_failure_keeps_other_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Missing silver TSE tables must not abort the detection."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_lake.read_silver_entities.side_effect = FileNotFoundError("no table")
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert "political_connection" not in {s["signal_type"] for s in signals}

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_adds_anomalous_geography_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Geography screening (O6, PR-D-09): a supplier whose municipality
        seat is past the strict 100 km gate signals via the silver chain."""
        contract = _silver_contract("C001", supplier_cnpj="11111111000111")
        contract["buyer"] = {"siafi_code": "2051", "city": "JOAO PESSOA", "uf": "PB"}
        mock_lake.read_silver_contracts.return_value = [contract]
        batches = {
            "sanctions": [],
            "campaign_donations": [],
            "candidacies": [],
            "rfb_municipalities": [[{"tom_code": "2531", "name": "RECIFE"}]],
            "municipalities": [
                [
                    {"name": "Recife", "uf": "PE", "ibge_code": "2611606",
                     "latitude": -8.0476, "longitude": -34.8770},
                    {"name": "João Pessoa", "uf": "PB", "ibge_code": "2507507",
                     "latitude": -7.1195, "longitude": -34.8450},
                ]
            ],
        }
        mock_lake.read_silver_entities.side_effect = lambda entity: iter(
            batches[entity]
        )
        # The establishments read is selective: only the supplier CNPJs of
        # the contracts (the full RFB table OOMKills the pod).
        mock_lake.read_establishments_for_cnpjs.return_value = [
            {"cnpj": "11111111000111", "municipio": "2531", "uf": "PE",
             "is_matriz": True}
        ]
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        mock_lake.load_municipalities.assert_called_once()
        mock_lake.read_establishments_for_cnpjs.assert_called_once_with(
            {"11111111000111"}
        )
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        geo = [s for s in signals if s["signal_type"] == "anomalous_geography"]
        assert len(geo) == 1
        assert geo[0]["entity_type"] == "supplier"
        assert geo[0]["entity_id"] == "11111111000111"
        assert geo[0]["score"] == 0.1033  # Recife x João Pessoa anchor
        assert summary["signals"] == len(signals)

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_geography_failure_keeps_other_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """A failure in the geo silver chain must not abort the detection."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        batches = {"sanctions": [], "campaign_donations": [], "candidacies": []}

        def _read(entity: str) -> Any:
            if entity not in batches:
                raise FileNotFoundError("no table")
            return iter(batches[entity])

        mock_lake.read_silver_entities.side_effect = _read
        mock_lake.read_establishments_for_cnpjs.side_effect = FileNotFoundError(
            "no table"
        )
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert "anomalous_geography" not in {s["signal_type"] for s in signals}

    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_arango_failure_keeps_statistical_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock
    ) -> None:
        """An ArangoDB failure must not abort the task nor drop the signals."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_get_db.side_effect = ConnectionError("arango down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert {s["signal_type"] for s in signals} == {"concentration"}

    @patch("capiba.pipeline.tasks.register_signals")
    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_registers_signals_for_triage(
        self,
        mock_lake: MagicMock,
        mock_get_db: MagicMock,
        mock_collusion: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """Computed signals must enter the editorial triage queue (O10)."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        mock_register.assert_called_once()
        assert mock_register.call_args.args[0] is mock_get_db.return_value
        assert len(mock_register.call_args.args[1]) == summary["signals"]

    @patch("capiba.pipeline.tasks.register_signals")
    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_triage_failure_does_not_abort(
        self,
        mock_lake: MagicMock,
        mock_get_db: MagicMock,
        mock_collusion: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """A triage failure must not abort the task nor drop the signals."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []
        mock_register.side_effect = RuntimeError("triage down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_stores_evidence_packages(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Signals must be stored as reproducible evidence packages (O9)."""
        contracts = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_lake.read_silver_contracts.return_value = contracts
        mock_collusion.return_value = []

        task_detect(ds="2026-01-01")

        self.mock_store_packages.assert_called_once()
        args = self.mock_store_packages.call_args.args
        assert args[1] == mock_lake.write_fraud_signals.call_args.args[0]
        assert args[2] == contracts
        assert args[3] == date(2026, 1, 1)
        # Graph snapshot (PR-D-03): eligibility rows flow to the packages.
        graph_snapshot = self.mock_store_packages.call_args.kwargs["graph_snapshot"]
        assert graph_snapshot == {
            "rows": mock_collusion.return_value,
            "min_wins": 3,
            "min_buyers": 1,
        }

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_evidence_failure_does_not_abort(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """A MinIO failure storing packages must not abort the task."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []
        self.mock_store_packages.side_effect = RuntimeError("minio down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_read_failure(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Silver read failures must yield an empty signal set."""
        mock_lake.read_silver_contracts.side_effect = RuntimeError("lake down")
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary == {"signals": 0}
        mock_lake.write_fraud_signals.assert_not_called()

    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_write_failure(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Gold write failures must not abort the task."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []
        mock_lake.write_fraud_signals.side_effect = RuntimeError("lake down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1


class TestTaskDbtRun:
    """Tests for the dbt run task."""

    @patch("capiba.pipeline.lake.silver_table_exists", return_value=True)
    @patch("capiba.pipeline.dbt_runner.run_dbt")
    def test_task_dbt_run(self, mock_run_dbt: MagicMock, mock_exists: MagicMock) -> None:
        """The task must invoke dbt and return an execution summary."""
        from capiba.config import DBT_PROJECT_DIR

        summary = task_dbt_run()

        mock_run_dbt.assert_called_once_with("run", select=None, exclude=None)
        assert summary == {
            "dbt": "run",
            "select": [],
            "exclude": [],
            "project_dir": DBT_PROJECT_DIR,
        }

    @patch("capiba.pipeline.lake.silver_table_exists", return_value=True)
    @patch("capiba.pipeline.dbt_runner.run_dbt")
    def test_task_dbt_run_with_select(
        self, mock_run_dbt: MagicMock, mock_exists: MagicMock
    ) -> None:
        """A model selection must reach run_dbt and the summary — and skips
        the TSE-silver existence check (the exclusion only applies to full
        runs)."""
        summary = task_dbt_run(select=["pod_usage_hourly"])

        mock_run_dbt.assert_called_once_with(
            "run", select=["pod_usage_hourly"], exclude=None
        )
        mock_exists.assert_not_called()
        assert summary["select"] == ["pod_usage_hourly"]

    @patch("capiba.pipeline.dbt_runner.run_dbt")
    def test_task_dbt_run_excludes_tse_marts_when_silvers_missing(
        self, mock_run_dbt: MagicMock
    ) -> None:
        """Full runs exclude the TSE-dependent marts while the TSE silvers
        do not exist (the mart SQL fails with TABLE_NOT_FOUND and would
        block the whole gold_detection DAG before the first monthly_tse
        load)."""
        with patch(
            "capiba.pipeline.lake.silver_table_exists",
            side_effect=lambda entity: entity != "campaign_donations",
        ):
            summary = task_dbt_run()

        mock_run_dbt.assert_called_once_with(
            "run", select=None, exclude=["political_connections"]
        )
        assert summary["exclude"] == ["political_connections"]


class TestTaskPostStep:
    """Tests for the post-step dispatcher (backfill skip)."""

    def test_skips_on_backfill_run(self) -> None:
        """Backfill runs skip post steps (deferred to a final regular run)."""
        from airflow.exceptions import AirflowSkipException

        dag_run = MagicMock()
        dag_run.run_type = "backfill"
        with pytest.raises(AirflowSkipException, match="backfill"):
            task_post_step("detect", "spec.yaml", dag_run=dag_run)

    def test_dispatches_on_regular_run(self) -> None:
        """Non-backfill runs dispatch the post step normally."""
        dag_run = MagicMock()
        dag_run.run_type = "manual"
        with patch("capiba.pipeline.tasks.task_detect") as mock_detect:
            mock_detect.return_value = {"signals": 0}
            summary = task_post_step("detect", "spec.yaml", dag_run=dag_run)
        assert summary == {"signals": 0}
        mock_detect.assert_called_once()

    def test_dispatches_without_dag_run(self) -> None:
        """A missing dag_run in the context dispatches normally."""
        with patch("capiba.pipeline.tasks.task_dbt_run") as mock_dbt:
            mock_dbt.return_value = {"dbt": "run"}
            summary = task_post_step("dbt_run", "spec.yaml")
        assert summary == {"dbt": "run"}

    def test_dbt_run_select_passthrough(self) -> None:
        """The dbt model selection reaches task_dbt_run."""
        with patch("capiba.pipeline.tasks.task_dbt_run") as mock_dbt:
            mock_dbt.return_value = {"dbt": "run"}
            task_post_step("dbt_run", "spec.yaml", select=["pod_usage_hourly"])
        mock_dbt.assert_called_once_with(select=["pod_usage_hourly"])


class TestRecordQualityBatch:
    """Tests for the quality-monitor hook of the validate task."""

    @patch("capiba.quality.monitor.QualityMonitor")
    def test_records_profile_and_batch_metrics(self, mock_cls: MagicMock) -> None:
        """A non-empty batch must be profiled, checked and recorded."""
        from capiba.pipeline.tasks import _record_quality_batch

        monitor = mock_cls.return_value
        contracts = [
            {"id": "C001", "amount": "100.0", "buyer": {"siafi_code": "1"}},
            {"id": "C002", "amount": "200.0", "buyer": {"siafi_code": "2"}},
        ]
        report = {
            "total": 2,
            "duplicates": 0,
            "normalization_errors": 1,
            "quality_rules": [
                {"rule": "r1", "severity": "error", "violations": 3},
                {"rule": "r2", "severity": "warning", "error": "boom"},
                {"rule": "r3", "severity": "info", "violations": 0},
            ],
        }

        _record_quality_batch("daily_ingestion", contracts, report)

        monitor.register_baseline.assert_called_once()
        assert monitor.register_baseline.call_args.args[0] == "pipeline:daily_ingestion"
        monitor.check.assert_called_once()
        monitor.record_batch.assert_called_once_with(
            "pipeline:daily_ingestion",
            {
                "total": 2,
                "duplicates": 0,
                "normalization_errors": 1,
                "quality_rule_failures": {"error": 1, "warning": 1},
            },
        )

    @patch("capiba.quality.monitor.QualityMonitor")
    def test_empty_batch_records_without_profile(self, mock_cls: MagicMock) -> None:
        """An empty batch skips the profile but still records the metrics."""
        from capiba.pipeline.tasks import _record_quality_batch

        monitor = mock_cls.return_value

        _record_quality_batch("daily_ingestion", [], {"total": 0, "duplicates": 0})

        monitor.register_baseline.assert_not_called()
        monitor.check.assert_not_called()
        monitor.record_batch.assert_called_once_with(
            "pipeline:daily_ingestion",
            {
                "total": 0,
                "duplicates": 0,
                "normalization_errors": 0,
                "quality_rule_failures": {},
            },
        )

    @patch("capiba.quality.monitor.QualityMonitor")
    def test_monitor_failure_is_swallowed(self, mock_cls: MagicMock) -> None:
        """A monitor failure (e.g. Redis down) must never break the task."""
        from capiba.pipeline.tasks import _record_quality_batch

        mock_cls.side_effect = RuntimeError("redis down")

        _record_quality_batch("daily_ingestion", [{"id": "C001"}], {"total": 1})
