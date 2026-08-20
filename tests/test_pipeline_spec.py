"""Tests for the declarative pipeline spec (YAML) model.

Responsibility: Validate loading, shorthand syntax, schema errors and
cross-validation against the capability registries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capiba.pipeline.spec import PipelineSpec, SpecError, load_spec

VALID_SPEC = """\
name: test_pipeline
schedule: "0 6 * * *"
window: previous_day
sources:
  - name: mock_pncp
  - name: mock_transparency
    window: current_month
formula: contracts_default
validate:
  ruleset: contract_rules
destinations:
  - lake_bronze
  - lake_silver
post_steps:
  - dbt_run
  - detect
"""


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadSpec:
    """Tests for load_spec happy paths."""

    def test_load_valid_spec(self, tmp_path: Path) -> None:
        """A valid YAML spec loads with all fields parsed."""
        spec = load_spec(_write(tmp_path, VALID_SPEC))

        assert spec.name == "test_pipeline"
        assert spec.schedule == "0 6 * * *"
        assert spec.window == "previous_day"
        assert [s.name for s in spec.sources] == ["mock_pncp", "mock_transparency"]
        assert spec.sources[1].window == "current_month"
        assert spec.formula == "contracts_default"
        assert spec.validation is not None
        assert spec.validation.ruleset == "contract_rules"
        assert [d.name for d in spec.destinations] == ["lake_bronze", "lake_silver"]
        assert [s.name for s in spec.post_steps] == ["dbt_run", "detect"]
        assert spec.post_steps[0].select == []

    def test_load_real_pipeline_yamls(self) -> None:
        """The shipped dags/pipelines specs must load cleanly."""
        pipelines_dir = Path(__file__).resolve().parent.parent / "dags" / "pipelines"
        specs = {p.stem: load_spec(p) for p in sorted(pipelines_dir.glob("*.yaml"))}

        assert set(specs) == {
            "daily_pncp",
            "daily_pncp_updates",
            "monthly_transparency",
            "monthly_federal_revenue",
            "hourly_pod_usage",
            "weekly_sanctions",
            "daily_querido_diario",
        }
        updates = specs["daily_pncp_updates"]
        assert updates.name == "daily_pncp_updates"
        assert updates.window == "previous_day"
        assert [s.name for s in updates.sources] == ["pncp_contract_updates"]
        assert [d.name for d in updates.destinations] == ["lake_bronze"]
        assert updates.post_steps == []  # amendment marts rebuilt by gold_detection
        daily = specs["daily_pncp"]
        assert daily.name == "daily_pncp"
        assert daily.schedule == "0 6 * * *"
        assert daily.window == "previous_day"
        assert [s.name for s in daily.sources] == ["pncp"]
        assert daily.post_steps == []  # dbt/detect live in the gold_detection DAG
        transparency = specs["monthly_transparency"]
        assert transparency.name == "monthly_transparency"
        assert transparency.schedule == "0 7 2 * *"
        assert transparency.window == "previous_month"
        assert [s.name for s in transparency.sources] == ["transparency"]
        assert transparency.post_steps == []
        assert specs["monthly_federal_revenue"].window == "previous_month"
        hourly = specs["hourly_pod_usage"]
        assert hourly.schedule == "7 * * * *"
        assert hourly.formula == "metrics_collect"
        assert [s.name for s in hourly.sources] == ["pod_usage"]
        # The hourly dbt_run refreshes only the usage marts (full runs
        # OOMKill Trino; contract marts belong to gold_detection).
        assert [s.name for s in hourly.post_steps] == ["dbt_run"]
        assert hourly.post_steps[0].select == ["pod_usage_hourly", "platform_cost_daily"]
        weekly = specs["weekly_sanctions"]
        assert weekly.schedule == "22 3 * * 2"
        assert weekly.formula == "entities_collect"
        assert [s.name for s in weekly.sources] == ["ceis", "cnep", "ceaf"]
        gazettes = specs["daily_querido_diario"]
        assert gazettes.schedule == "41 4 * * *"
        assert gazettes.formula == "documents_collect"
        assert gazettes.window == "previous_day"
        assert [s.name for s in gazettes.sources] == ["querido_diario"]
        assert gazettes.sources[0].params == {"territory_id": "2611606"}
        assert gazettes.validation is not None
        assert gazettes.validation.ruleset == "gazette_rules"

    def test_string_shorthand(self, tmp_path: Path) -> None:
        """Plain strings are accepted as shorthand for name-only entries."""
        spec = load_spec(
            _write(
                tmp_path,
                """\
name: minimal
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
""",
            )
        )

        assert spec.sources[0].name == "mock_pncp"
        assert spec.window == "previous_day"  # default
        assert spec.schedule is None
        assert spec.validation is None

    def test_source_params(self, tmp_path: Path) -> None:
        """Free-form source params are preserved."""
        spec = load_spec(
            _write(
                tmp_path,
                """\
name: with_params
sources:
  - name: pncp
    params:
      agency_cnpj: "12345678000190"
formula: contracts_default
destinations: [lake_bronze]
""",
            )
        )

        assert spec.sources[0].params == {"agency_cnpj": "12345678000190"}


class TestLoadSpecErrors:
    """Tests for load_spec validation errors."""

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML fails with a clear error."""
        path = _write(tmp_path, "name: [unclosed")

        with pytest.raises(SpecError, match="Invalid YAML"):
            load_spec(path)

    def test_non_mapping_yaml(self, tmp_path: Path) -> None:
        """A YAML document that is not a mapping fails clearly."""
        path = _write(tmp_path, "- just\n- a\n- list\n")

        with pytest.raises(SpecError, match="must be a YAML mapping"):
            load_spec(path)

    def test_unknown_source(self, tmp_path: Path) -> None:
        """An unknown source name fails listing the known ones."""
        path = _write(
            tmp_path,
            """\
name: bad_source
sources: [fonte_inexistente]
formula: contracts_default
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="unknown source 'fonte_inexistente'"):
            load_spec(path)

    def test_unknown_formula(self, tmp_path: Path) -> None:
        """An unknown formula name fails listing the known ones."""
        path = _write(
            tmp_path,
            """\
name: bad_formula
sources: [mock_pncp]
formula: formula_inexistente
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="unknown formula 'formula_inexistente'"):
            load_spec(path)

    def test_unknown_destination(self, tmp_path: Path) -> None:
        """An unknown destination name fails."""
        path = _write(
            tmp_path,
            """\
name: bad_dest
sources: [mock_pncp]
formula: contracts_default
destinations: [planilha_excel]
""",
        )

        with pytest.raises(SpecError, match="unknown destination 'planilha_excel'"):
            load_spec(path)

    def test_unknown_ruleset(self, tmp_path: Path) -> None:
        """An unknown validation ruleset fails."""
        path = _write(
            tmp_path,
            """\
name: bad_ruleset
sources: [mock_pncp]
formula: contracts_default
validate:
  ruleset: regras_inexistentes
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="unknown ruleset 'regras_inexistentes'"):
            load_spec(path)

    def test_unknown_transformation(self, tmp_path: Path) -> None:
        """An unknown transformation fails."""
        path = _write(
            tmp_path,
            """\
name: bad_transform
sources: [mock_pncp]
formula: contracts_default
transformations: [transformacao_inexistente]
destinations: [lake_silver]
""",
        )

        with pytest.raises(
            SpecError, match="unknown transformation 'transformacao_inexistente'"
        ):
            load_spec(path)

    def test_known_transformation_from_package(self, tmp_path: Path) -> None:
        """Transformations resolve from the capiba.transformations package."""
        path = _write(
            tmp_path,
            """\
name: good_transform
sources: [mock_pncp]
formula: contracts_default
transformations:
  - name: filter_by_min_value
    params:
      min_value: 1000
destinations: [lake_silver]
""",
        )

        spec = load_spec(path)

        assert spec.transformations[0].params == {"min_value": 1000}

    def test_invalid_window(self, tmp_path: Path) -> None:
        """An unknown window name fails schema validation."""
        path = _write(
            tmp_path,
            """\
name: bad_window
window: last_week
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="Invalid pipeline spec"):
            load_spec(path)

    def test_invalid_post_step(self, tmp_path: Path) -> None:
        """An unknown post step fails schema validation."""
        path = _write(
            tmp_path,
            """\
name: bad_post
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
post_steps: [spark_submit]
""",
        )

        with pytest.raises(SpecError, match="Invalid pipeline spec"):
            load_spec(path)

    def test_post_step_dbt_run_with_select(self, tmp_path: Path) -> None:
        """dbt_run accepts a dbt model selection in the mapping form."""
        path = _write(
            tmp_path,
            """\
name: scoped_dbt
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
post_steps:
  - name: dbt_run
    select: [pod_usage_hourly, platform_cost_daily]
""",
        )

        spec = load_spec(path)

        assert [s.name for s in spec.post_steps] == ["dbt_run"]
        assert spec.post_steps[0].select == ["pod_usage_hourly", "platform_cost_daily"]

    def test_post_step_select_only_on_dbt_run(self, tmp_path: Path) -> None:
        """A model selection on a non-dbt post step fails validation."""
        path = _write(
            tmp_path,
            """\
name: bad_select
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
post_steps:
  - name: detect
    select: [pod_usage_hourly]
""",
        )

        with pytest.raises(SpecError, match="does not support 'select'"):
            load_spec(path)

    def test_dump_formula_requires_dump_source(self, tmp_path: Path) -> None:
        """file_dump with a record-only source fails."""
        path = _write(
            tmp_path,
            """\
name: bad_dump
sources: [mock_pncp]
formula: file_dump
destinations: [lake_bronze]
""",
        )

        with pytest.raises(SpecError, match="has no dump downloader"):
            load_spec(path)

    def test_contracts_formula_requires_record_source(self, tmp_path: Path) -> None:
        """contracts_default with a dump-only source fails."""
        path = _write(
            tmp_path,
            """\
name: bad_contracts
sources: [federal_revenue]
formula: contracts_default
destinations: [lake_bronze]
""",
        )

        with pytest.raises(SpecError, match="has no record fetcher"):
            load_spec(path)

    def test_entities_collect_valid(self, tmp_path: Path) -> None:
        """The real CEIS/CNEP sources validate against entities_collect."""
        path = _write(
            tmp_path,
            """\
name: entities_ok
window: all
sources: [ceis, cnep]
formula: entities_collect
destinations: [lake_bronze, lake_silver]
""",
        )

        spec = load_spec(path)

        assert spec.formula == "entities_collect"
        assert [s.name for s in spec.sources] == ["ceis", "cnep"]

    def test_entities_formula_requires_record_source(self, tmp_path: Path) -> None:
        """entities_collect with a dump-only source fails."""
        path = _write(
            tmp_path,
            """\
name: bad_entities
window: all
sources: [federal_revenue]
formula: entities_collect
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="has no record fetcher"):
            load_spec(path)

    def test_entities_formula_requires_entity_normalizer(self, tmp_path: Path) -> None:
        """entities_collect with a source without entity normalizer fails."""
        path = _write(
            tmp_path,
            """\
name: bad_entities_normalizer
window: all
sources: [mock_pncp]
formula: entities_collect
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="has no registered entity normalizer"):
            load_spec(path)

    def test_name_pattern(self, tmp_path: Path) -> None:
        """DAG-unsafe names fail schema validation."""
        path = _write(
            tmp_path,
            """\
name: "Bad Name!"
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
""",
        )

        with pytest.raises(SpecError, match="Invalid pipeline spec"):
            load_spec(path)


class TestPipelineSpecModel:
    """Direct model-level tests."""

    def test_multiple_errors_aggregated(self, tmp_path: Path) -> None:
        """All registry errors are reported at once."""
        path = _write(
            tmp_path,
            """\
name: multi_error
sources: [fonte_a]
formula: formula_b
destinations: [destino_c]
""",
        )

        with pytest.raises(SpecError) as exc_info:
            load_spec(path)

        message = str(exc_info.value)
        assert "unknown source 'fonte_a'" in message
        assert "unknown formula 'formula_b'" in message
        assert "unknown destination 'destino_c'" in message

    def test_extra_fields_forbidden(self) -> None:
        """Unknown YAML keys fail schema validation."""
        with pytest.raises(Exception, match="extra"):
            PipelineSpec.model_validate(
                {
                    "name": "x",
                    "sources": ["mock_pncp"],
                    "formula": "contracts_default",
                    "destinations": ["lake_silver"],
                    "surprise": True,
                }
            )
