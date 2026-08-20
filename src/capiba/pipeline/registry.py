"""Registries that resolve YAML names to Python callables.

Responsibility: the declarative pipeline specs (``dags/pipelines/*.yaml``)
reference sources, normalizers, rulesets, transformations, destinations and
formulas by name; this module owns the name → implementation mapping so
that no Python code is needed to declare a new pipeline, only to register
new capabilities.

Formula and destination implementations live in ``capiba.pipeline.runner``
(to keep this module free of lake/task dependencies) and register
themselves into ``FORMULA_REGISTRY``/``DESTINATION_REGISTRY`` at import
time — importing the runner (which ``capiba.pipeline.spec.load_spec`` does)
guarantees the registries are populated before validation.

Dependencies: ingestion crawlers/normalizer/mocks, quality validators.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from capiba.ingestion.cnpj import entity_for_zip as cnpj_entity_for_zip
from capiba.ingestion.cnpj import parse_cnpj_zip
from capiba.ingestion.crawler_federal_revenue import download_cnpj_dump
from capiba.ingestion.crawler_pncp import fetch_contract_updates as pncp_fetch_updates
from capiba.ingestion.crawler_pncp import fetch_contracts as pncp_fetch_contracts
from capiba.ingestion.crawler_querido_diario import (
    fetch_gazettes as querido_diario_fetch_gazettes,
)
from capiba.ingestion.crawler_transparency import (
    fetch_contracts as transparency_fetch_contracts,
)
from capiba.ingestion.crawler_transparency import (
    fetch_sanctions,
)
from capiba.ingestion.crawler_tse import download_tse_dump
from capiba.ingestion.mock import mock_pncp, mock_transparency
from capiba.ingestion.normalizer import Contract
from capiba.ingestion.pod_usage import fetch_pod_usage
from capiba.ingestion.sanctions import Sanction
from capiba.ingestion.tse import entity_for_dump as tse_entity_for_dump
from capiba.ingestion.tse import parse_tse_zip
from capiba.quality.validators import CONTRACT_RULES, GAZETTE_RULES, ValidationRule

if TYPE_CHECKING:
    from datetime import date

logger = logging.getLogger(__name__)

# fetch(start, end, **params) -> raw records; ``start``/``end`` may be None
# for the unbounded ``all`` window.
RecordFetcher = Callable[..., list[dict[str, Any]]]
# download(destination, reference_month, **params) -> downloaded files.
DumpDownloader = Callable[..., list[Path]]
# transform(records, **params) -> transformed records.
TransformFn = Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True)
class SourceDef:
    """Capabilities of a registered source.

    Record sources implement ``fetch`` (contracts-style formulas); dump
    sources implement ``download`` (file_dump formula). A source may
    implement both.
    """

    fetch: RecordFetcher | None = None
    download: DumpDownloader | None = None


def _fetch_pncp(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """Adapts the PNCP crawler to the registry window signature."""
    if start is None or end is None:
        raise ValueError("The pncp source requires a bounded window")
    return pncp_fetch_contracts(start_date=start, end_date=end, **params)


def _fetch_pncp_contract_updates(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """Adapts the PNCP contract-updates crawler to the window signature."""
    if start is None or end is None:
        raise ValueError("The pncp_contract_updates source requires a bounded window")
    return pncp_fetch_updates(start_date=start, end_date=end, **params)


def _fetch_transparency(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """Adapts the Transparency crawler to the registry window signature."""
    if start is None or end is None:
        raise ValueError("The transparency source requires a bounded window")
    return transparency_fetch_contracts(
        start_date=start.isoformat(), end_date=end.isoformat(), **params
    )


def _fetch_mock_pncp(
    start: date | None, end: date | None, **_params: Any
) -> list[dict[str, Any]]:
    """Mock PNCP source: ignores the window and returns sample records."""
    return mock_pncp()


def _fetch_mock_transparency(
    start: date | None, end: date | None, **_params: Any
) -> list[dict[str, Any]]:
    """Mock Transparency source: ignores the window and returns sample records."""
    return mock_transparency()


def _fetch_pod_usage(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """Pod usage source: point-in-time snapshot; ignores the window."""
    return fetch_pod_usage(**params)


def _fetch_ceis(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """CEIS sanction list: full snapshot; ignores the window."""
    return fetch_sanctions("ceis", **params)


def _fetch_cnep(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """CNEP sanction list: full snapshot; ignores the window."""
    return fetch_sanctions("cnep", **params)


def _fetch_ceaf(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """CEAF expulsion list: full snapshot; ignores the window."""
    return fetch_sanctions("ceaf", **params)


def _fetch_querido_diario(
    start: date | None, end: date | None, **params: Any
) -> list[dict[str, Any]]:
    """Adapts the Querido Diário crawler to the registry window signature."""
    if start is None or end is None:
        raise ValueError("The querido_diario source requires a bounded window")
    return querido_diario_fetch_gazettes(start_date=start, end_date=end, **params)


SOURCE_REGISTRY: dict[str, SourceDef] = {
    "pncp": SourceDef(fetch=_fetch_pncp),
    "pncp_contract_updates": SourceDef(fetch=_fetch_pncp_contract_updates),
    "transparency": SourceDef(fetch=_fetch_transparency),
    "federal_revenue": SourceDef(download=download_cnpj_dump),
    "tse": SourceDef(download=download_tse_dump),
    "mock_pncp": SourceDef(fetch=_fetch_mock_pncp),
    "mock_transparency": SourceDef(fetch=_fetch_mock_transparency),
    "pod_usage": SourceDef(fetch=_fetch_pod_usage),
    "ceis": SourceDef(fetch=_fetch_ceis),
    "cnep": SourceDef(fetch=_fetch_cnep),
    "ceaf": SourceDef(fetch=_fetch_ceaf),
    "querido_diario": SourceDef(fetch=_fetch_querido_diario),
}

# Normalizer per source: raw record -> unified Contract. Mock sources reuse
# the normalizer of the source they impersonate. ``pncp_contract_updates``
# shares the ``/v1/contratos`` payload, so it reuses ``from_pncp`` — its
# pipeline is bronze-only (PR-D-05): the normalized records are computed
# but never persisted to the silver.
NORMALIZER_REGISTRY: dict[str, Callable[[dict[str, Any]], Contract]] = {
    "pncp": Contract.from_pncp,
    "pncp_contract_updates": Contract.from_pncp,
    "transparency": Contract.from_transparency,
    "mock_pncp": Contract.from_pncp,
    "mock_transparency": Contract.from_transparency,
}

RULESET_REGISTRY: dict[str, list[ValidationRule]] = {
    "contract_rules": CONTRACT_RULES,
    "gazette_rules": GAZETTE_RULES,
}


@dataclass(frozen=True)
class EntityNormalizerDef:
    """Maps an entity source to its silver table and record normalizer.

    Used by the ``entities_collect`` formula: ``entity`` is the silver
    entity table (see ``capiba.pipeline.lake.ENTITY_TABLES``) and
    ``normalize`` validates one raw record into the entity model.
    """

    entity: str
    normalize: Callable[[dict[str, Any]], Any]


# Entity normalizer per source (entities_collect formula): the CEIS/CNEP/CEAF
# sanction lists all land in the silver ``sanctions`` table.
ENTITY_NORMALIZER_REGISTRY: dict[str, EntityNormalizerDef] = {
    "ceis": EntityNormalizerDef(entity="sanctions", normalize=Sanction.from_ceis),
    "cnep": EntityNormalizerDef(entity="sanctions", normalize=Sanction.from_cnep),
    "ceaf": EntityNormalizerDef(entity="sanctions", normalize=Sanction.from_ceaf),
}

@dataclass(frozen=True)
class DumpParserDef:
    """Streaming parser of a dump source and its file → entity mapping.

    Used by the ``file_dump`` formula when the spec declares the
    lake_silver/arangodb_graph destinations: ``parse(zip_path, chunk_size)``
    yields (entity, records, errors) chunks and ``entity_for_file`` maps a
    dump file name to its silver entity (None for non-entity files).
    """

    parse: Callable[..., Any]
    entity_for_file: Callable[[str], str | None]


# Streaming parser per dump source.
DUMP_PARSER_REGISTRY: dict[str, DumpParserDef] = {
    "federal_revenue": DumpParserDef(
        parse=parse_cnpj_zip, entity_for_file=cnpj_entity_for_zip
    ),
    "tse": DumpParserDef(parse=parse_tse_zip, entity_for_file=tse_entity_for_dump),
}

# Explicit transformation entries; names not present here are resolved by
# importing ``capiba.transformations.<name>`` and reading its ``transform``
# function (see ``get_transformation``).
TRANSFORMATION_REGISTRY: dict[str, TransformFn] = {}

# Populated by capiba.pipeline.runner at import time:
# formula name -> formula(spec, execution_date, steps) -> FormulaResult.
FORMULA_REGISTRY: dict[str, Callable[..., Any]] = {}
# destination name -> handler(spec, execution_date, result) -> dict summary.
DESTINATION_REGISTRY: dict[str, Callable[..., Any]] = {}


def get_transformation(name: str) -> TransformFn:
    """Resolves a transformation name to its callable.

    Looks up ``TRANSFORMATION_REGISTRY`` first, then tries to import
    ``capiba.transformations.<name>`` and use its ``transform`` function.

    Raises:
        KeyError: If the name matches neither an entry nor a module.
    """
    if name in TRANSFORMATION_REGISTRY:
        return TRANSFORMATION_REGISTRY[name]
    try:
        module = importlib.import_module(f"capiba.transformations.{name}")
    except ImportError as exc:
        raise KeyError(name) from exc
    transform = getattr(module, "transform", None)
    if not callable(transform):
        raise KeyError(name) from None
    TRANSFORMATION_REGISTRY[name] = cast(TransformFn, transform)
    return TRANSFORMATION_REGISTRY[name]
