"""Tests for the declarative pipeline runner.

Responsibility: Validate the contracts_default and file_dump formulas
end-to-end with mock sources and a local SQLite Iceberg catalog (no infra),
the per-step metrics of the run report, window overrides and failure
semantics (source failure fails the run; lake failures are best-effort).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from capiba.pipeline import lake, tasks
from capiba.pipeline.registry import (
    NORMALIZER_REGISTRY,
    SOURCE_REGISTRY,
    SourceDef,
)
from capiba.pipeline.runner import (
    PipelineRunError,
    run_pipeline,
)
from capiba.pipeline.spec import PipelineSpec
from capiba.pipeline.window import DateRange

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


def _spec(**overrides: Any) -> PipelineSpec:
    """Builds a contracts_default spec over the mock sources."""
    data: dict[str, Any] = {
        "name": "test_run",
        "window": "previous_day",
        "sources": ["mock_pncp", "mock_transparency"],
        "formula": "contracts_default",
        "validate": {"ruleset": "contract_rules"},
        "destinations": ["lake_bronze", "lake_silver"],
    }
    data.update(overrides)
    return PipelineSpec.model_validate(data)


class TestContractsDefaultFormula:
    """End-to-end tests of the contracts_default formula."""

    def test_run_with_mock_sources(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """Mock sources flow through crawl/normalize/validate to the lake."""
        report = run_pipeline(_spec(), RUN_DATE)

        assert report.success is True
        assert report.pipeline == "test_run"
        assert report.execution_date == RUN_DATE
        assert [s.name for s in report.steps] == [
            "crawl_mock_pncp",
            "crawl_mock_transparency",
            "normalize",
            "validate",
            "destination_lake_bronze",
            "destination_lake_silver",
        ]

        crawl = report.steps[0]
        assert crawl.rows_out == 1
        assert crawl.errors == 0
        assert crawl.duration_seconds >= 0

        normalize = report.steps[2]
        assert normalize.rows_in == 2
        assert normalize.rows_out == 2
        assert normalize.errors == 0

        assert report.validation is not None
        assert report.validation["total"] == 2
        assert report.validation["valid"] is True
        assert "checksum" in report.validation
        # The contract_rules ruleset ran over the flattened contracts
        assert report.validation["quality_rules"]
        rule_names = {r["rule"] for r in report.validation["quality_rules"]}
        assert "positive_value" in rule_names

        # Bronze raw tables and the silver contracts table were written
        bronze = report.outputs["destination_lake_bronze"]
        assert bronze["sources"] == ["mock_pncp", "mock_transparency"]
        silver_rows = lake.read_silver_contracts()
        assert len(silver_rows) == 2

    def test_gold_report_destination(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """The gold_report destination publishes the run report object."""
        report = run_pipeline(_spec(destinations=["gold_report"]), RUN_DATE)

        key = report.outputs["destination_gold_report"]["key"]
        assert key.startswith("reports/test_run/dt=2026-01-15/")
        buckets = {c.args[0] for c in mock_client.put_object.mock_calls}
        assert buckets == {"capiba-gold"}

    def test_arangodb_destination_best_effort(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ArangoDB the graph destination degrades to a step error."""
        # Força a queda do ArangoDB: o teste não pode depender de infra
        # local estar parada (ex.: port-forward do cluster ativo na 8529).
        def _no_db() -> Any:
            raise ConnectionError("arangodb down")

        monkeypatch.setattr(tasks, "get_capiba_db", _no_db)

        report = run_pipeline(_spec(destinations=["arangodb_graph"]), RUN_DATE)

        assert report.success is True
        step = report.steps[-1]
        assert step.name == "destination_arangodb_graph"
        assert step.errors == 1
        assert "error" in report.outputs["destination_arangodb_graph"]

    def test_lake_failure_is_best_effort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lake destination failures do not fail the run."""
        client = MagicMock()
        client.put_object.side_effect = RuntimeError("lake down")
        monkeypatch.setattr(lake, "get_client", lambda: client)

        report = run_pipeline(_spec(), RUN_DATE)

        assert report.success is True
        bronze_step = next(
            s for s in report.steps if s.name == "destination_lake_bronze"
        )
        assert bronze_step.errors == 2

    def test_transformation_step(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """Declared transformations run between normalize and validate."""
        spec = _spec(
            transformations=[{"name": "filter_by_min_value", "params": {"min_value": 20000}}]
        )

        report = run_pipeline(spec, RUN_DATE)

        transform = next(s for s in report.steps if s.name == "transform_filter_by_min_value")
        assert transform.rows_in == 2
        assert transform.rows_out == 1  # the 15000 PNCP mock is dropped
        silver_rows = lake.read_silver_contracts()
        assert len(silver_rows) == 1
        assert report.validation is not None
        assert report.validation["total"] == 1

    def test_per_source_window(
        self, mock_client: MagicMock, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A source window overrides the pipeline default window."""
        seen: dict[str, Any] = {}

        def spy_fetch(start: date | None, end: date | None, **_: Any) -> list[dict[str, Any]]:
            seen["range"] = (start, end)
            return []

        monkeypatch.setitem(SOURCE_REGISTRY, "spy", SourceDef(fetch=spy_fetch))
        monkeypatch.setitem(NORMALIZER_REGISTRY, "spy", lambda raw: None)  # type: ignore[return-value]

        spec = _spec(sources=[{"name": "spy", "window": "current_month"}])

        report = run_pipeline(spec, RUN_DATE)

        assert report.success is True
        assert seen["range"] == (date(2026, 1, 1), date(2026, 2, 1))

    def test_window_override(
        self, mock_client: MagicMock, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit window override wins over the declared windows."""
        seen: dict[str, Any] = {}

        def spy_fetch(start: date | None, end: date | None, **_: Any) -> list[dict[str, Any]]:
            seen["range"] = (start, end)
            return []

        monkeypatch.setitem(SOURCE_REGISTRY, "spy", SourceDef(fetch=spy_fetch))
        monkeypatch.setitem(NORMALIZER_REGISTRY, "spy", lambda raw: None)  # type: ignore[return-value]

        spec = _spec(sources=[{"name": "spy", "window": "current_month"}])
        override = DateRange(start=date(2026, 3, 1), end=date(2026, 3, 31))

        run_pipeline(spec, RUN_DATE, window_override=override)

        assert seen["range"] == (date(2026, 3, 1), date(2026, 3, 31))

    def test_source_failure_fails_run(
        self, mock_client: MagicMock, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing source raises PipelineRunError carrying the report."""

        def broken_fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            raise ConnectionError("source down")

        monkeypatch.setitem(SOURCE_REGISTRY, "broken", SourceDef(fetch=broken_fetch))
        spec = _spec(sources=["broken"])

        with pytest.raises(PipelineRunError, match="source down") as exc_info:
            run_pipeline(spec, RUN_DATE)

        report = exc_info.value.report
        assert report.success is False
        assert report.steps[0].name == "crawl_broken"
        assert report.steps[0].error == "source down"


def _fake_zip(path: Path) -> Path:
    """Writes a minimal valid ZIP file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("data.csv", "a;b")
    path.write_bytes(buffer.getvalue())
    return path


class TestFileDumpFormula:
    """Tests of the file_dump formula (Federal Revenue dump)."""

    def _spec(self) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_dump",
                "window": "previous_month",
                "sources": ["federal_revenue"],
                "formula": "file_dump",
                "destinations": ["lake_bronze"],
            }
        )

    def test_download_and_manifest(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Downloaded files land in bronze and are recorded in the manifest."""

        def fake_download(destination: Path, *_args: Any, **_kwargs: Any) -> list[Path]:
            return [_fake_zip(destination / "Cnaes.zip")]

        monkeypatch.setitem(
            SOURCE_REGISTRY, "federal_revenue", SourceDef(download=fake_download)
        )

        report = run_pipeline(self._spec(), date(2026, 2, 2))

        assert report.success is True
        assert report.outputs["federal_revenue_reference_month"] == "2026-01"
        assert report.outputs["federal_revenue_files"] == 1

        step = report.steps[0]
        assert step.name == "download_federal_revenue"
        assert step.rows_out == 1

        # The ZIP object landed in the bronze bucket with sha256 metadata
        file_puts = [
            c for c in mock_client.put_object.mock_calls if "files/" in c.args[1]
        ]
        assert len(file_puts) == 1
        assert file_puts[0].args[1].endswith("federal_revenue/files/dt=2026-02-02/Cnaes.zip")

        # The manifest payload went to the bronze audit copy + raw table
        manifest = report.steps[-1]
        assert manifest.name == "destination_lake_bronze"
        assert manifest.errors == 0
        catalog = lake.get_catalog("bronze")
        table = catalog.load_table("capiba.raw_federal_revenue")
        rows = table.scan().to_pandas().to_dict("records")
        assert len(rows) == 1
        assert "2026-01" in rows[0]["payload_json"]

    def test_empty_manifest_fails_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty download fails the run loudly (no silent success)."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=lambda *_a, **_k: []),
        )

        with pytest.raises(PipelineRunError, match="No files downloaded"):
            run_pipeline(self._spec(), date(2026, 2, 2))

    def test_file_dump_requires_bounded_window(self) -> None:
        """The all window is invalid for dump sources."""
        spec = PipelineSpec.model_validate(
            {
                "name": "test_dump_all",
                "window": "all",
                "sources": ["federal_revenue"],
                "formula": "file_dump",
                "destinations": ["lake_bronze"],
            }
        )

        with pytest.raises(PipelineRunError, match="month-bounded window"):
            run_pipeline(spec, RUN_DATE)


class TestMetricsCollectFormula:
    """Tests of the metrics_collect formula (pod usage snapshots)."""

    def _spec(self) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_metrics",
                "window": "all",
                "sources": ["pod_usage"],
                "formula": "metrics_collect",
                "destinations": ["lake_bronze"],
            }
        )

    def test_collect_to_bronze(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The snapshot flows to the bronze raw_pod_usage table."""
        snapshot = [
            {
                "pod": "capiba-api-abc",
                "container": "api",
                "cpu_millicores": 42,
                "memory_bytes": 100 * 1024**2,
                "collected_at": "2026-01-15T12:07:00+00:00",
            }
        ]

        def fake_fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return snapshot

        monkeypatch.setitem(SOURCE_REGISTRY, "pod_usage", SourceDef(fetch=fake_fetch))

        report = run_pipeline(self._spec(), RUN_DATE)

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "collect_pod_usage",
            "destination_lake_bronze",
        ]
        assert report.steps[0].rows_out == 1

        catalog = lake.get_catalog("bronze")
        table = catalog.load_table("capiba.raw_pod_usage")
        rows = table.scan().to_pandas().to_dict("records")
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload[0]["pod"] == "capiba-api-abc"
        assert payload[0]["cpu_millicores"] == 42

    def test_empty_snapshot_is_a_successful_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty snapshot (graceful degradation) is not a run failure."""
        monkeypatch.setitem(
            SOURCE_REGISTRY, "pod_usage", SourceDef(fetch=lambda *_a, **_k: [])
        )

        report = run_pipeline(self._spec(), RUN_DATE)

        assert report.success is True
        assert report.steps[0].rows_out == 0


class TestTermsCollectFormula:
    """Tests of the terms_collect formula (PR-D-05b pilot probe)."""

    def _spec(self) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_terms",
                "window": "all",
                "sources": [
                    {
                        "name": "pncp_contract_terms",
                        "params": {"include_flagged": True, "siafi_codes": ["2531"]},
                    }
                ],
                "formula": "terms_collect",
                "destinations": ["lake_bronze"],
            }
        )

    def test_cohort_crawl_then_terms_persist(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The cohort flows to the persist step; the bronze keeps the cohort."""
        from capiba.pipeline import runner

        cohort = [
            {
                "numeroControlePNCP": "00394460000141-1-000012/2026",
                "cohort": "flagged+pilot",
            }
        ]
        captured: dict[str, Any] = {}

        def fake_fetch(*_args: Any, **params: Any) -> list[dict[str, Any]]:
            captured["params"] = params
            return [dict(record) for record in cohort]

        monkeypatch.setitem(
            SOURCE_REGISTRY, "pncp_contract_terms", SourceDef(fetch=fake_fetch)
        )
        persist = MagicMock(
            return_value={
                "source": "pncp_contract_terms",
                "terms_fetched": 1,
                "terms_skipped": 0,
                "errors": 0,
            }
        )
        monkeypatch.setattr(runner, "persist_contract_terms", persist)

        report = run_pipeline(self._spec(), RUN_DATE)

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "crawl_pncp_contract_terms",
            "persist_pncp_contract_terms_terms",
            "destination_lake_bronze",
        ]
        # The window is ignored; the spec params reach the cohort fetcher.
        assert captured["params"] == {
            "include_flagged": True,
            "siafi_codes": ["2531"],
        }
        persist.assert_called_once_with(
            "pncp_contract_terms", cohort, run_date=RUN_DATE
        )
        assert report.steps[1].rows_out == 1
        assert report.outputs["pncp_contract_terms_terms"]["terms_fetched"] == 1

        catalog = lake.get_catalog("bronze")
        table = catalog.load_table("capiba.raw_pncp_contract_terms")
        rows = table.scan().to_pandas().to_dict("records")
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload[0]["numeroControlePNCP"] == cohort[0]["numeroControlePNCP"]

    def test_persist_errors_are_step_metrics_not_run_failure(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-contract fetch failures are counted; the run still succeeds."""
        from capiba.pipeline import runner

        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "pncp_contract_terms",
            SourceDef(
                fetch=lambda *_a, **_k: [
                    {"numeroControlePNCP": "00394460000141-1-000012/2026"}
                ]
            ),
        )
        monkeypatch.setattr(
            runner,
            "persist_contract_terms",
            MagicMock(
                return_value={
                    "source": "pncp_contract_terms",
                    "terms_fetched": 0,
                    "terms_skipped": 0,
                    "errors": 1,
                }
            ),
        )

        report = run_pipeline(self._spec(), RUN_DATE)

        assert report.success is True
        assert report.steps[1].errors == 1

    def test_source_failure_fails_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cohort enumeration failure fails the run with the crawl step."""

        def broken_fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("trino down")

        monkeypatch.setitem(
            SOURCE_REGISTRY, "pncp_contract_terms", SourceDef(fetch=broken_fetch)
        )

        with pytest.raises(PipelineRunError) as exc_info:
            run_pipeline(self._spec(), RUN_DATE)

        report = exc_info.value.report
        assert report.success is False
        assert [s.name for s in report.steps] == ["crawl_pncp_contract_terms"]
        assert report.steps[0].error == "trino down"


class TestTaskRunPipeline:
    """Tests for the Airflow wrapper task_run_pipeline."""

    def test_runs_spec_and_post_steps(
        self, mock_client: MagicMock, local_catalog: Path, tmp_path: Path
    ) -> None:
        """The wrapper resolves the run date, runs the spec and post steps."""
        from capiba.pipeline.tasks import task_run_pipeline

        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            """\
name: wrapped
window: previous_day
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
post_steps: [dbt_run, detect]
""",
            encoding="utf-8",
        )

        with (
            patch("capiba.pipeline.tasks.task_dbt_run") as mock_dbt,
            patch("capiba.pipeline.tasks.task_detect") as mock_detect,
        ):
            mock_dbt.return_value = {"dbt": "run"}
            mock_detect.return_value = {"signals": 0}
            summary = task_run_pipeline(str(spec_path), ds="2026-01-15")

        assert summary["pipeline"] == "wrapped"
        assert summary["success"] is True
        assert summary["post_steps"] == {"dbt_run": {"dbt": "run"}, "detect": {"signals": 0}}
        # The run used the resolved previous-day window against the silver table
        silver_rows = lake.read_silver_contracts()
        assert len(silver_rows) == 1
        assert str(silver_rows[0]["dt"]) == "2026-01-15"


def _cnpj_zip(path: Path, member: str, rows: list[str]) -> Path:
    """Writes a fixture CNPJ dump ZIP with one member."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(member, "\n".join(rows))
    path.write_bytes(buffer.getvalue())
    return path


def _fake_cnpj_download(destination: Path, *_args: Any, **_kwargs: Any) -> list[Path]:
    """Fake federal_revenue download with entity + reference ZIPs."""
    return [
        _cnpj_zip(
            destination / "Empresas0.zip",
            "K3241.K03200Y0.D50610.EMPRECSV",
            [
                "12345678;EMPRESA A;2062;49;1000,00;05;",
                "87654321;EMPRESA B;2062;49;2000,00;05;PE",
                "999;INVALIDA;2062;49;10,00;05;",
            ],
        ),
        _cnpj_zip(
            destination / "Socios0.zip",
            "K3241.K03200Y0.D50610.SOCIOCSV",
            ["12345678;2;JOAO SILVA;***123456**;22;20150101;;;;;5"],
        ),
        _fake_zip(destination / "Cnaes.zip"),
    ]


TSE_HEADER = (
    "SQ_PRESTADOR_CONTAS;SQ_CANDIDATO;NM_CANDIDATO;SG_PARTIDO;DS_CARGO;NM_UE;"
    "SG_UF;DS_ORIGEM_RECEITA;SQ_RECEITA;DT_RECEITA;VR_RECEITA;"
    "NR_CPF_CNPJ_DOADOR;NM_DOADOR;NM_DOADOR_RFB;NR_CPF_CNPJ_DOADOR_ORIGINARIO"
)
TSE_PJ_ROW = (
    "111;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de pessoas jurídicas;555;2024-08-15T00:00:00;50.000,00;"
    "12345678000190;EMPRESA DOADORA;EMPRESA DOADORA LTDA;"
)
TSE_PF_ROW = (
    "111;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de pessoas físicas;556;15/09/2024;1.000,50;12345678901;"
    "JOAO D***;JOAO DA SILVA;"
)
TSE_BAD_ROW = (
    "111;9001;JOANA CANDIDATA;XX;Prefeito;RECIFE;PE;"
    "Recursos de pessoas físicas;557;31/99/2024;10,00;12345678901;X;X;"
)


TSE_CAND_HEADER = (
    "SQ_CANDIDATO;NM_CANDIDATO;SG_PARTIDO;DS_CARGO;CD_UE;NM_UE;SG_UF;"
    "DS_SITUACAO_TOTALIZACAO_TURNO"
)
TSE_CAND_ELECTED_ROW = "9001;JOANA CANDIDATA;XX;Prefeito;25313;RECIFE;PE;Eleito"
TSE_CAND_DEFEATED_ROW = "9002;ZE DERROTADO;YY;Prefeito;25313;RECIFE;PE;Não eleito"


def _tse_zip(path: Path, rows: list[str]) -> Path:
    """Writes a fixture TSE dump ZIP with one receitas member (latin1)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "receitas_candidatos_2024_BRASIL.csv",
            "\n".join([TSE_HEADER, *rows]).encode("latin1"),
        )
    path.write_bytes(buffer.getvalue())
    return path


def _tse_cand_zip(path: Path, rows: list[str]) -> Path:
    """Writes a fixture consulta_cand ZIP with one BRASIL member (latin1)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "consulta_cand_2024_BRASIL.csv",
            "\n".join([TSE_CAND_HEADER, *rows]).encode("latin1"),
        )
    path.write_bytes(buffer.getvalue())
    return path


def _fake_tse_download(destination: Path, *_args: Any, **_kwargs: Any) -> list[Path]:
    """Fake tse download: prestação de contas + consulta_cand ZIPs."""
    return [
        _tse_zip(
            destination / "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            [TSE_PJ_ROW, TSE_BAD_ROW, TSE_PF_ROW],
        ),
        _tse_cand_zip(
            destination / "consulta_cand_2024.zip",
            [TSE_CAND_ELECTED_ROW, TSE_CAND_DEFEATED_ROW],
        ),
    ]


def _mock_graph_db() -> MagicMock:
    """A mocked ArangoDB whose collections are stable per-name mocks."""
    db = MagicMock()
    collections: dict[str, MagicMock] = {}
    db.collection.side_effect = lambda name: collections.setdefault(
        name, MagicMock(name=name)
    )
    db._capiba_collections = collections
    return db


class TestFileDumpNormalize:
    """Tests of the streaming normalize step of the file_dump formula."""

    def _spec(self, destinations: list[str]) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_dump_silver",
                "window": "previous_month",
                "sources": ["federal_revenue"],
                "formula": "file_dump",
                "destinations": destinations,
            }
        )

    def test_normalize_to_silver(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Entity ZIPs flow (chunked) to the silver tables; Cnaes is skipped."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=_fake_cnpj_download),
        )

        report = run_pipeline(
            self._spec(["lake_bronze", "lake_silver"]), date(2026, 2, 2)
        )

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "download_federal_revenue",
            "normalize_federal_revenue",
            "destination_lake_bronze",
            "destination_lake_silver",
        ]
        normalize = report.steps[1]
        assert normalize.rows_out == 3
        assert normalize.errors == 1  # the invalid Empresas row

        assert report.outputs["federal_revenue_entities"] == {
            "companies": 2,
            "partners": 1,
        }
        silver = report.outputs["destination_lake_silver"]
        assert silver["entities"] == {"federal_revenue": {"companies": 2, "partners": 1}}

        companies = [r for batch in lake.read_silver_entities("companies") for r in batch]
        assert {r["cnpj_basico"] for r in companies} == {"12345678", "87654321"}
        partners = [r for batch in lake.read_silver_entities("partners") for r in batch]
        assert len(partners) == 1
        assert partners[0]["nome"] == "JOAO SILVA"

    def test_graph_destination_loads_from_silver(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """arangodb_graph bulk-upserts companies/partners read from the silver."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=_fake_cnpj_download),
        )
        db = _mock_graph_db()
        monkeypatch.setattr(tasks, "get_capiba_db", lambda: db)

        report = run_pipeline(
            self._spec(["lake_bronze", "arangodb_graph"]), date(2026, 2, 2)
        )

        assert report.success is True
        graph = report.outputs["destination_arangodb_graph"]
        assert graph == {
            "companies": 2,
            "persons": 1,
            "edges": 1,
            "errors": 0,
            "same_as": 0,
        }
        collections: dict[str, MagicMock] = db._capiba_collections
        company_docs = collections["companies"].import_bulk.call_args.args[0]
        assert {d["_key"] for d in company_docs} == {"12345678", "87654321"}
        edge_docs = collections["ownership"].import_bulk.call_args.args[0]
        assert edge_docs[0]["_to"] == "companies/12345678"

    def test_graph_destination_best_effort(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ArangoDB the graph destination degrades to a step error."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=_fake_cnpj_download),
        )

        def _no_db() -> Any:
            raise ConnectionError("arangodb down")

        monkeypatch.setattr(tasks, "get_capiba_db", _no_db)

        report = run_pipeline(self._spec(["arangodb_graph"]), date(2026, 2, 2))

        assert report.success is True
        step = report.steps[-1]
        assert step.name == "destination_arangodb_graph"
        assert step.errors == 1
        assert "error" in report.outputs["destination_arangodb_graph"]

    def test_silver_write_failure_is_best_effort(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing silver chunk write is counted, not fatal to the run."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=_fake_cnpj_download),
        )
        monkeypatch.setattr(
            lake,
            "write_silver_entities",
            MagicMock(side_effect=RuntimeError("lake down")),
        )

        report = run_pipeline(self._spec(["lake_silver"]), date(2026, 2, 2))

        assert report.success is True
        normalize = next(s for s in report.steps if s.name == "normalize_federal_revenue")
        assert normalize.rows_out == 0
        # 1 invalid row + 2 failed chunk writes (companies + partners chunks)
        assert normalize.errors == 3

    def test_no_normalize_without_entity_destinations(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Specs without lake_silver/arangodb_graph keep the old behavior."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=_fake_cnpj_download),
        )

        report = run_pipeline(self._spec(["lake_bronze"]), date(2026, 2, 2))

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "download_federal_revenue",
            "destination_lake_bronze",
        ]


class TestTseFileDumpNormalize:
    """Tests of the file_dump formula over the TSE snapshot source (O8)."""

    def _spec(self, destinations: list[str]) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_tse_dump",
                "window": "previous_month",
                "sources": [{"name": "tse", "params": {"year": 2024}}],
                "formula": "file_dump",
                "destinations": destinations,
            }
        )

    def test_normalize_to_silver(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The receitas member flows (chunked) to silver campaign_donations."""
        download = MagicMock(side_effect=_fake_tse_download)
        monkeypatch.setitem(SOURCE_REGISTRY, "tse", SourceDef(download=download))

        report = run_pipeline(
            self._spec(["lake_bronze", "lake_silver"]), date(2026, 2, 2)
        )

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "download_tse",
            "normalize_tse",
            "destination_lake_bronze",
            "destination_lake_silver",
        ]
        normalize = report.steps[1]
        assert normalize.rows_out == 4
        assert normalize.errors == 1  # the unparseable date row

        # The TSE source derives its file list from params.year; the CNPJ
        # default file list must not leak into other dump sources.
        assert download.call_args.kwargs["files"] is None
        assert download.call_args.kwargs["year"] == 2024

        assert report.outputs["tse_entities"] == {
            "campaign_donations": 2,
            "candidacies": 2,
        }
        candidacies = [
            r for batch in lake.read_silver_entities("candidacies") for r in batch
        ]
        assert {r["totalization_status"] for r in candidacies} == {
            "Eleito",
            "Não eleito",
        }
        assert all(r["election_year"] == 2024 for r in candidacies)
        donations = [
            r for batch in lake.read_silver_entities("campaign_donations") for r in batch
        ]
        assert {r["donor_document"] for r in donations} == {
            "12345678000190",
            "12345678901",
        }
        pj = next(r for r in donations if r["donor_document"] == "12345678000190")
        assert pj["donor_name"] == "EMPRESA DOADORA LTDA"
        assert pj["election_year"] == 2024
        assert pj["office"] == "Prefeito"
        assert str(pj["amount"]) == "50000.00"
        assert str(pj["donation_date"]) == "2024-08-15"


class TestTaskNormalizeDump:
    """Tests for the Airflow task_normalize_dump wrapper."""

    def _spec_file(self, tmp_path: Path, destinations: str) -> Path:
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            f"""\
name: dump_task
window: previous_month
sources: [federal_revenue]
formula: file_dump
destinations: [{destinations}]
""",
            encoding="utf-8",
        )
        return spec_path

    def test_noop_without_entity_destinations(self, tmp_path: Path) -> None:
        """Specs without silver/graph destinations are a no-op summary."""
        from capiba.pipeline.tasks import task_normalize_dump

        ti = MagicMock()
        summary = task_normalize_dump(
            "federal_revenue", str(self._spec_file(tmp_path, "lake_bronze")),
            ti=ti, ds="2026-02-02",
        )

        assert summary == {
            "source": "federal_revenue",
            "entities": {},
            "skipped": True,
        }

    def test_noop_for_source_without_parser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sources not in the dump parser registry are a no-op."""
        from capiba.pipeline.tasks import task_normalize_dump

        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "dump_no_parser",
            SourceDef(download=lambda *_a, **_k: []),
        )
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            """\
name: dump_task_no_parser
window: previous_month
sources: [dump_no_parser]
formula: file_dump
destinations: [lake_silver]
""",
            encoding="utf-8",
        )

        summary = task_normalize_dump(
            "dump_no_parser", str(spec_path), ti=MagicMock(), ds="2026-02-02"
        )

        assert summary["skipped"] is True

    def test_reads_bronze_files_and_writes_silver(
        self, tmp_path: Path, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Manifest files are read back from bronze and parsed to silver."""
        from capiba.pipeline.tasks import task_normalize_dump

        zip_path = _cnpj_zip(
            tmp_path / "Empresas0.zip",
            "K3241.K03200Y0.D50610.EMPRECSV",
            ["12345678;EMPRESA A;2062;49;1000,00;05;PE"],
        )
        data = zip_path.read_bytes()
        mock_read = MagicMock(return_value=data)
        monkeypatch.setattr(lake, "read_bronze_file", mock_read)

        ti = MagicMock()
        ti.xcom_pull.return_value = {
            "reference_month": "2026-01",
            "files": [
                {
                    "file": "Empresas0.zip",
                    "bytes": len(data),
                    "sha256": "x",
                    "lake_key": "federal_revenue/files/dt=2026-02-02/Empresas0.zip",
                },
                {"file": "Cnaes.zip", "bytes": 1, "sha256": "y", "lake_key": "ref"},
            ],
        }

        summary = task_normalize_dump(
            "federal_revenue",
            str(self._spec_file(tmp_path, "lake_silver")),
            ti=ti,
            ds="2026-02-02",
        )

        assert summary["entities"] == {"companies": 1}
        assert summary["errors"] == 0
        # The reference ZIP is skipped without touching the lake
        assert mock_read.call_count == 1
        rows = [r for batch in lake.read_silver_entities("companies") for r in batch]
        assert [r["cnpj_basico"] for r in rows] == ["12345678"]
        ti.xcom_push.assert_called_once_with(
            key="entities_federal_revenue", value=summary
        )

    def test_deletes_entity_partitions_before_parsing(
        self, tmp_path: Path, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retries re-parse from scratch: partitions are deleted first."""
        from capiba.pipeline.tasks import task_normalize_dump

        zip_path = _cnpj_zip(
            tmp_path / "Empresas0.zip",
            "K3241.K03200Y0.D50610.EMPRECSV",
            ["12345678;EMPRESA A;2062;49;1000,00;05;PE"],
        )
        monkeypatch.setattr(
            lake, "read_bronze_file", MagicMock(return_value=zip_path.read_bytes())
        )
        mock_delete = MagicMock()
        monkeypatch.setattr(lake, "delete_silver_entities_partition", mock_delete)

        ti = MagicMock()
        ti.xcom_pull.return_value = {
            "reference_month": "2026-01",
            "files": [
                {
                    "file": "Empresas0.zip",
                    "bytes": 1,
                    "sha256": "x",
                    "lake_key": "federal_revenue/files/dt=2026-02-02/Empresas0.zip",
                },
                {"file": "Cnaes.zip", "bytes": 1, "sha256": "y", "lake_key": "ref"},
            ],
        }

        summary = task_normalize_dump(
            "federal_revenue",
            str(self._spec_file(tmp_path, "lake_silver")),
            ti=ti,
            ds="2026-02-02",
        )

        # Only entity files get a partition delete (Cnaes.zip is skipped);
        # sources without a year param keep the whole-partition delete.
        mock_delete.assert_called_once_with(
            "companies", date(2026, 2, 2), election_year=None
        )
        assert summary["entities"] == {"companies": 1}

    def test_tse_delete_scoped_to_election_year(
        self, tmp_path: Path, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multi-year TSE: the delete carries params.year of the run."""
        from capiba.pipeline.tasks import task_normalize_dump

        zip_path = _tse_zip(
            tmp_path / "prestacao_de_contas_eleitorais_candidatos_2022.zip",
            [TSE_PJ_ROW],
        )
        monkeypatch.setattr(
            lake, "read_bronze_file", MagicMock(return_value=zip_path.read_bytes())
        )
        mock_delete = MagicMock()
        monkeypatch.setattr(lake, "delete_silver_entities_partition", mock_delete)

        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            """\
name: tse_dump_task
window: previous_month
sources: [{name: tse, params: {year: 2022}}]
formula: file_dump
destinations: [lake_silver]
""",
            encoding="utf-8",
        )
        ti = MagicMock()
        ti.xcom_pull.return_value = {
            "reference_month": "2026-01",
            "files": [
                {
                    "file": "prestacao_de_contas_eleitorais_candidatos_2022.zip",
                    "bytes": 1,
                    "sha256": "x",
                    "lake_key": "tse/files/dt=2026-02-02/x.zip",
                },
            ],
        }

        summary = task_normalize_dump(
            "tse", str(spec_path), ti=ti, ds="2026-02-02"
        )

        mock_delete.assert_called_once_with(
            "campaign_donations", date(2026, 2, 2), election_year=2022
        )
        assert summary["entities"] == {"campaign_donations": 1}

    def test_tse_delete_defaults_to_config_year(
        self, tmp_path: Path, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A TSE spec without params.year scopes the delete to the config year."""
        from capiba.config import TSE_ELECTION_YEAR
        from capiba.pipeline.tasks import task_normalize_dump

        zip_path = _tse_zip(
            tmp_path / "prestacao_de_contas_eleitorais_candidatos_2024.zip",
            [TSE_PJ_ROW],
        )
        monkeypatch.setattr(
            lake, "read_bronze_file", MagicMock(return_value=zip_path.read_bytes())
        )
        mock_delete = MagicMock()
        monkeypatch.setattr(lake, "delete_silver_entities_partition", mock_delete)

        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            """\
name: tse_dump_task_default_year
window: previous_month
sources: [tse]
formula: file_dump
destinations: [lake_silver]
""",
            encoding="utf-8",
        )
        ti = MagicMock()
        ti.xcom_pull.return_value = {
            "reference_month": "2026-01",
            "files": [
                {
                    "file": "prestacao_de_contas_eleitorais_candidatos_2024.zip",
                    "bytes": 1,
                    "sha256": "x",
                    "lake_key": "tse/files/dt=2026-02-02/x.zip",
                },
            ],
        }

        task_normalize_dump("tse", str(spec_path), ti=ti, ds="2026-02-02")

        mock_delete.assert_called_once_with(
            "campaign_donations", date(2026, 2, 2), election_year=TSE_ELECTION_YEAR
        )


class TestTaskDestinationFileDump:
    """Tests for the file_dump branches of task_destination."""

    def _spec_file(self, tmp_path: Path, destinations: str) -> Path:
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            f"""\
name: dump_dest
window: previous_month
sources: [federal_revenue]
formula: file_dump
destinations: [{destinations}]
""",
            encoding="utf-8",
        )
        return spec_path

    def test_arangodb_graph_loads_cnpj_entities(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The graph destination delegates to the silver CNPJ graph load."""
        from capiba.pipeline.tasks import task_destination

        persist = MagicMock(return_value={"companies": 2, "partners": 1})
        monkeypatch.setattr(tasks, "persist_cnpj_entities", persist)

        summary = task_destination(
            "arangodb_graph",
            str(self._spec_file(tmp_path, "arangodb_graph")),
            ti=MagicMock(),
            ds="2026-02-02",
        )

        assert summary == {"companies": 2, "partners": 1}
        persist.assert_called_once_with(execution_date="2026-02-02")

    def test_lake_silver_reports_normalize_counts(self, tmp_path: Path) -> None:
        """The silver destination only reports the streaming write counts."""
        from capiba.pipeline.tasks import task_destination

        ti = MagicMock()
        ti.xcom_pull.return_value = {
            "source": "federal_revenue",
            "entities": {"companies": 2},
            "errors": 0,
        }

        summary = task_destination(
            "lake_silver",
            str(self._spec_file(tmp_path, "lake_silver")),
            ti=ti,
            ds="2026-02-02",
        )

        assert summary == {
            "entities": {
                "federal_revenue": {
                    "source": "federal_revenue",
                    "entities": {"companies": 2},
                    "errors": 0,
                }
            }
        }


def _ceis_raw(sanction_id: int = 1) -> dict[str, Any]:
    """A minimal raw CEIS payload (documented CeisDTO shape)."""
    return {
        "id": sanction_id,
        "dataInicioSancao": "01/01/2025",
        "dataFimSancao": "01/01/2027",
        "tipoSancao": {"descricaoPortal": "Inidoneidade"},
        "orgaoSancionador": {"nome": "MINISTERIO DA FAZENDA", "siglaUf": "DF"},
        "sancionado": {
            "nome": "EMPRESA SANCIONADA LTDA",
            "codigoFormatado": "12.345.678/0001-90",
        },
    }


class TestEntitiesCollectFormula:
    """Tests of the entities_collect formula (CEIS/CNEP sanction lists)."""

    def _spec(self, destinations: list[str]) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_entities",
                "window": "all",
                "sources": ["ceis", "cnep"],
                "formula": "entities_collect",
                "destinations": destinations,
            }
        )

    def _fake_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replaces the CEIS/CNEP fetches with offline fakes."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "ceis",
            SourceDef(fetch=lambda *_a, **_k: [_ceis_raw(1), _ceis_raw(2)]),
        )
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "cnep",
            SourceDef(fetch=lambda *_a, **_k: [_ceis_raw(3)]),
        )

    def test_collect_to_silver(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CEIS/CNEP records flow to the bronze payloads and silver table."""
        self._fake_sources(monkeypatch)

        report = run_pipeline(
            self._spec(["lake_bronze", "lake_silver"]), RUN_DATE
        )

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "crawl_ceis",
            "normalize_ceis",
            "crawl_cnep",
            "normalize_cnep",
            "destination_lake_bronze",
            "destination_lake_silver",
        ]
        normalize = report.steps[1]
        assert normalize.rows_in == 2
        assert normalize.rows_out == 2
        assert normalize.errors == 0

        assert report.outputs["ceis_entities"] == {"sanctions": 2}
        assert report.outputs["cnep_entities"] == {"sanctions": 1}
        silver = report.outputs["destination_lake_silver"]
        assert silver["entities"] == {
            "ceis": {"sanctions": 2},
            "cnep": {"sanctions": 1},
        }

        rows = [r for batch in lake.read_silver_entities("sanctions") for r in batch]
        assert len(rows) == 3
        assert {r["list_name"] for r in rows} == {"ceis", "cnep"}
        assert all(r["cnpj"] == "12345678000190" for r in rows)

        # The raw payloads landed in the bronze raw tables
        bronze = report.outputs["destination_lake_bronze"]
        assert bronze["sources"] == ["ceis", "cnep"]
        catalog = lake.get_catalog("bronze")
        raw_ceis = catalog.load_table("capiba.raw_ceis").scan().to_arrow()
        assert raw_ceis.num_rows == 1

    def test_normalization_errors_are_counted(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Records that fail normalization are skipped and counted."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "ceis",
            SourceDef(fetch=lambda *_a, **_k: [_ceis_raw(1), "not-a-dict"]),
        )
        monkeypatch.setitem(
            SOURCE_REGISTRY, "cnep", SourceDef(fetch=lambda *_a, **_k: [])
        )

        report = run_pipeline(self._spec(["lake_silver"]), RUN_DATE)

        assert report.success is True
        normalize = report.steps[1]
        assert normalize.rows_out == 1
        assert normalize.errors == 1
        rows = [r for batch in lake.read_silver_entities("sanctions") for r in batch]
        assert len(rows) == 1

    def test_silver_write_failure_is_best_effort(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing silver write is counted, not fatal to the run."""
        self._fake_sources(monkeypatch)
        monkeypatch.setattr(
            lake,
            "write_silver_entities",
            MagicMock(side_effect=RuntimeError("lake down")),
        )

        report = run_pipeline(self._spec(["lake_silver"]), RUN_DATE)

        assert report.success is True
        for step in report.steps:
            if step.name.startswith("normalize_"):
                assert step.errors == 1

    def test_source_failure_fails_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing sanction source raises PipelineRunError."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "ceis",
            SourceDef(
                fetch=MagicMock(side_effect=ConnectionError("transparency down"))
            ),
        )
        monkeypatch.setitem(
            SOURCE_REGISTRY, "cnep", SourceDef(fetch=lambda *_a, **_k: [])
        )

        with pytest.raises(PipelineRunError, match="transparency down"):
            run_pipeline(self._spec(["lake_bronze"]), RUN_DATE)


def _qd_raw(n: int) -> dict[str, Any]:
    """Raw Querido Diário gazette record (Recife, IBGE 2611606)."""
    return {
        "territory_id": "2611606",
        "territory_name": "Recife",
        "state_code": "PE",
        "date": f"2026-01-{14 + n:02d}",
        "edition": str(n),
        "is_extra_edition": False,
        "scraped_at": "2026-01-16T03:53:53",
        "url": f"https://data.queridodiario.ok.org.br/2611606/2026-01-{14 + n:02d}/abc{n}.pdf",
        "txt_url": f"https://data.queridodiario.ok.org.br/2611606/2026-01-{14 + n:02d}/abc{n}.txt",
        "excerpts": [],
    }


class TestDocumentsCollectFormula:
    """Tests of the documents_collect formula (Querido Diário gazettes, O7)."""

    def _spec(self, destinations: list[str]) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_documents",
                "window": "previous_day",
                "sources": [{"name": "querido_diario", "params": {"territory_id": "2611606"}}],
                "formula": "documents_collect",
                "validate": {"ruleset": "gazette_rules"},
                "destinations": destinations,
            }
        )

    def _fake_source(
        self, monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]
    ) -> None:
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "querido_diario",
            SourceDef(fetch=lambda *_a, **_k: records),
        )
        monkeypatch.setattr(
            tasks, "download_gazette_text", MagicMock(return_value=b"plain text")
        )

    def test_crawl_download_validate_to_bronze(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gazettes are crawled, their texts persisted and the records validated."""
        self._fake_source(monkeypatch, [_qd_raw(1), _qd_raw(2)])

        report = run_pipeline(self._spec(["lake_bronze"]), RUN_DATE)

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "crawl_querido_diario",
            "download_querido_diario_texts",
            "validate",
            "destination_lake_bronze",
        ]
        texts = report.outputs["querido_diario_texts"]
        assert texts["texts_downloaded"] == 2
        assert texts["texts_skipped"] == 0
        assert texts["errors"] == 0

        # The raw records were enriched with the bronze file name
        download = report.steps[1]
        assert download.rows_in == 2
        assert download.rows_out == 2

        # The declared ruleset validated the raw records
        assert report.validation is not None
        assert report.validation["total"] == 2
        assert report.validation["valid"] is True
        assert {r["rule"] for r in report.validation["quality_rules"]} == {
            "valid_territory",
            "date_present",
            "file_url_present",
            "text_url_present",
        }

        # The raw payload landed in the bronze raw table
        catalog = lake.get_catalog("bronze")
        raw = catalog.load_table("capiba.raw_querido_diario").scan().to_arrow()
        assert raw.num_rows == 1

    def test_texts_already_in_bronze_are_skipped(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A retried run skips texts already persisted for the run date."""
        record = _qd_raw(1)
        self._fake_source(monkeypatch, [record])
        filename = tasks.text_file_name(record)
        monkeypatch.setattr(
            lake,
            "list_bronze_files",
            MagicMock(
                return_value=[f"querido_diario/files/dt=2026-01-15/{filename}"]
            ),
        )

        report = run_pipeline(self._spec(["lake_bronze"]), RUN_DATE)

        assert report.success is True
        texts = report.outputs["querido_diario_texts"]
        assert texts["texts_downloaded"] == 0
        assert texts["texts_skipped"] == 1
        tasks.download_gazette_text.assert_not_called()  # type: ignore[attr-defined]

    def test_download_failure_is_best_effort(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing text download is counted, not fatal to the run."""
        self._fake_source(monkeypatch, [_qd_raw(1)])
        monkeypatch.setattr(
            tasks,
            "download_gazette_text",
            MagicMock(side_effect=ConnectionError("data host down")),
        )

        report = run_pipeline(self._spec(["lake_bronze"]), RUN_DATE)

        assert report.success is True
        texts = report.outputs["querido_diario_texts"]
        assert texts["errors"] == 1
        assert texts["texts_downloaded"] == 0
        assert report.validation is not None
        assert report.validation["normalization_errors"] == 1

    def test_source_failure_fails_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing document source raises PipelineRunError."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "querido_diario",
            SourceDef(fetch=MagicMock(side_effect=ConnectionError("QD down"))),
        )

        with pytest.raises(PipelineRunError, match="QD down"):
            run_pipeline(self._spec(["lake_bronze"]), RUN_DATE)
