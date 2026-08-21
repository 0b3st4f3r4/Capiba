"""Declarative pipeline specification (YAML) model.

Responsibility: define the pydantic models behind
``dags/pipelines/*.yaml`` and validate them at load time — both
structurally (pydantic) and against the capability registries (unknown
source/formula/destination/ruleset/transformation fails with a clear
error before any run).

A pipeline spec declares *what* to run; ``capiba.pipeline.runner`` decides
*how* (formulas), and ``dags/pipeline_factory.py`` turns each spec into an
Airflow DAG.

Dependencies: pydantic, PyYAML, capiba.pipeline.registry/window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from capiba.pipeline.registry import (
    DESTINATION_REGISTRY,
    ENTITY_NORMALIZER_REGISTRY,
    FORMULA_REGISTRY,
    NORMALIZER_REGISTRY,
    RULESET_REGISTRY,
    SOURCE_REGISTRY,
    get_transformation,
)
from capiba.pipeline.window import WindowKind

PostStep = Literal["dbt_run", "detect"]


class SpecError(ValueError):
    """Raised when a pipeline spec file is invalid."""


class PostStepSpec(BaseModel):
    """A post-step entry: registry name and optional parameters.

    ``select`` restricts the dbt models built by ``dbt_run`` (passed to
    ``dbt run --select``); empty means the whole project. Only ``dbt_run``
    accepts options — a full run rebuilds every mart, so hourly pipelines
    should select just the marts their fresh data feeds.
    """

    model_config = ConfigDict(extra="forbid")

    name: PostStep
    select: list[str] = Field(default_factory=list)


class SourceSpec(BaseModel):
    """A source entry: registry name, optional window override and params.

    ``window`` overrides the pipeline-level default for this source only —
    e.g. the daily pipeline crawls PNCP for the previous day but the
    Transparency Portal for the whole current month.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    window: WindowKind | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class TransformationSpec(BaseModel):
    """A named transformation with free-form parameters."""

    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ValidateSpec(BaseModel):
    """Validation step configuration: which quality ruleset to apply."""

    model_config = ConfigDict(extra="forbid")

    ruleset: str


class DestinationSpec(BaseModel):
    """A destination entry: registry name and free-form parameters."""

    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


def _expand_names(value: Any) -> Any:
    """Accepts plain strings as shorthand for ``{"name": <str>}`` entries."""
    if isinstance(value, list):
        return [{"name": item} if isinstance(item, str) else item for item in value]
    return value


class PipelineSpec(BaseModel):
    """Root model of a declarative ingestion pipeline.

    Mirrors a ``dags/pipelines/*.yaml`` file: schedule and default window,
    the sources to crawl, the formula orchestrating the steps, optional
    validation/transformations, destinations and Airflow-side post steps.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    description: str | None = None
    schedule: str | None = None
    window: WindowKind = "previous_day"
    sources: Annotated[list[SourceSpec], Field(min_length=1)]
    formula: str
    # YAML key is ``validate``; the field is named ``validation`` to avoid
    # shadowing ``BaseModel.validate``.
    validation: ValidateSpec | None = Field(None, alias="validate")
    transformations: list[TransformationSpec] = Field(default_factory=list)
    destinations: Annotated[list[DestinationSpec], Field(min_length=1)]
    post_steps: list[PostStepSpec] = Field(default_factory=list)

    @field_validator("sources", "transformations", "destinations", "post_steps", mode="before")
    @classmethod
    def _expand_shorthand(cls, value: Any) -> Any:
        return _expand_names(value)


def _cross_validate(spec: PipelineSpec, origin: str) -> None:
    """Validates registry references, raising ``SpecError`` with context."""
    errors: list[str] = []

    for source in spec.sources:
        source_def = SOURCE_REGISTRY.get(source.name)
        if source_def is None:
            errors.append(
                f"unknown source '{source.name}'"
                f" (known: {sorted(SOURCE_REGISTRY)})"
            )
            continue
        if spec.formula == "contracts_default":
            if source_def.fetch is None:
                errors.append(
                    f"source '{source.name}' has no record fetcher;"
                    " formula 'contracts_default' requires one"
                )
            if source.name not in NORMALIZER_REGISTRY:
                errors.append(f"source '{source.name}' has no registered normalizer")
        elif spec.formula == "file_dump" and source_def.download is None:
            errors.append(
                f"source '{source.name}' has no dump downloader;"
                " formula 'file_dump' requires one"
            )
        elif spec.formula == "metrics_collect" and source_def.fetch is None:
            errors.append(
                f"source '{source.name}' has no record fetcher;"
                " formula 'metrics_collect' requires one"
            )
        elif spec.formula == "documents_collect" and source_def.fetch is None:
            errors.append(
                f"source '{source.name}' has no record fetcher;"
                " formula 'documents_collect' requires one"
            )
        elif spec.formula == "terms_collect" and source_def.fetch is None:
            errors.append(
                f"source '{source.name}' has no record fetcher;"
                " formula 'terms_collect' requires one"
            )
        elif spec.formula == "entities_collect":
            if source_def.fetch is None:
                errors.append(
                    f"source '{source.name}' has no record fetcher;"
                    " formula 'entities_collect' requires one"
                )
            if source.name not in ENTITY_NORMALIZER_REGISTRY:
                errors.append(
                    f"source '{source.name}' has no registered entity normalizer"
                )

    if spec.formula not in FORMULA_REGISTRY:
        errors.append(
            f"unknown formula '{spec.formula}' (known: {sorted(FORMULA_REGISTRY)})"
        )

    for destination in spec.destinations:
        if destination.name not in DESTINATION_REGISTRY:
            errors.append(
                f"unknown destination '{destination.name}'"
                f" (known: {sorted(DESTINATION_REGISTRY)})"
            )

    if spec.validation and spec.validation.ruleset not in RULESET_REGISTRY:
        errors.append(
            f"unknown ruleset '{spec.validation.ruleset}'"
            f" (known: {sorted(RULESET_REGISTRY)})"
        )

    for transformation in spec.transformations:
        try:
            get_transformation(transformation.name)
        except KeyError:
            errors.append(
                f"unknown transformation '{transformation.name}'"
                " (no registry entry and no capiba.transformations module)"
            )

    for step in spec.post_steps:
        if step.name != "dbt_run" and step.select:
            errors.append(
                f"post step '{step.name}' does not support 'select'"
                " (only dbt_run accepts a dbt model selection)"
            )

    if errors:
        details = "\n  - ".join(errors)
        raise SpecError(f"Invalid pipeline spec '{origin}':\n  - {details}")


def load_spec(path: str | Path) -> PipelineSpec:
    """Loads and validates a pipeline spec from a YAML file.

    Args:
        path: Path of the YAML spec.

    Returns:
        The validated ``PipelineSpec``.

    Raises:
        SpecError: On YAML syntax errors, schema violations or unknown
            registry references.
    """
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"Invalid YAML in pipeline spec '{path}': {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"Pipeline spec '{path}' must be a YAML mapping")

    # Importing the runner populates the formula/destination registries.
    from capiba.pipeline import runner  # noqa: F401

    try:
        spec = PipelineSpec.model_validate(data)
    except ValidationError as exc:
        raise SpecError(f"Invalid pipeline spec '{path}': {exc}") from exc

    _cross_validate(spec, str(path))
    return spec
