"""OpenLineage asset converter for Capiba.

Airflow 3 ships with built-in converters for ``s3://`` and ``file://`` assets,
which split the URI into namespace + name. For Capiba that places every lake
dataset under its own namespace (``s3://capiba-bronze``, ``s3://capiba-silver``,
``s3://capiba-gold``), so the Marquez UI shows an empty ``/datasets`` page when
viewed from the job namespace ``capiba``.

This module registers a custom ``capiba://`` scheme so that all Capiba datasets
are emitted under the single OpenLineage namespace ``capiba``. The host/path
portion of the URI becomes the dataset name, preserving the logical location
(bronze, silver, gold, arangodb, source, etc.).

The converter is registered at DAG-parse time by importing this module from
``dags/pipeline_factory.py``.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

CAPIBA_NAMESPACE = "capiba"


def _normalize_capiba_uri(uri: Any) -> Any:
    """Identity normalizer for ``capiba://`` URIs.

    Airflow calls the registered normalizer when building ``Asset.normalized_uri``.
    Returning the URI unchanged is enough for the scheme to be recognized as
    valid.
    """
    return uri


def _convert_capiba_asset(asset: Any, lineage_context: Any) -> Any:
    """Convert a ``capiba://`` Asset into an OpenLineage Dataset.

    The dataset namespace is always ``capiba``; the name is the host + path of
    the URI (without a leading slash).
    """
    from airflow.providers.common.compat.openlineage.facet import (
        Dataset as OpenLineageDataset,
    )

    parsed = urlsplit(asset.uri)
    name = (parsed.netloc + parsed.path).lstrip("/")
    return OpenLineageDataset(namespace=CAPIBA_NAMESPACE, name=name)


def _register_provider_manager(manager: Any) -> None:
    """Register the Capiba handlers on a concrete provider manager."""
    handlers = getattr(manager, "_asset_uri_handlers", None)
    converters = getattr(manager, "_asset_to_openlineage_converters", None)

    if handlers is not None and "capiba" not in handlers:
        handlers["capiba"] = _normalize_capiba_uri
        logger.debug("Registered Capiba URI handler")

    if converters is not None and "capiba" not in converters:
        converters["capiba"] = _convert_capiba_asset
        logger.debug("Registered Capiba OpenLineage converter")


def _provider_manager_classes() -> list[type[Any]]:
    """Return the Airflow provider manager classes to register with."""
    classes: list[type[Any]] = []
    try:
        from airflow.sdk.providers_manager_runtime import (
            ProvidersManagerTaskRuntime,
        )

        classes.append(ProvidersManagerTaskRuntime)
    except Exception:  # pragma: no cover - depends on Airflow internals
        logger.debug("ProvidersManagerTaskRuntime unavailable", exc_info=True)

    try:
        from airflow.providers_manager import ProvidersManager

        classes.append(ProvidersManager)
    except Exception:  # pragma: no cover - depends on Airflow internals
        logger.debug("ProvidersManager unavailable", exc_info=True)

    return classes


def register_capiba_openlineage() -> None:
    """Register the ``capiba://`` scheme with Airflow's provider manager.

    Tries the task-runtime provider manager first (Airflow 3) and falls back to
    the legacy ``ProvidersManager`` for compatibility.
    """
    for cls in _provider_manager_classes():
        try:
            _register_provider_manager(cls())
        except Exception:  # pragma: no cover - depends on Airflow internals
            logger.debug("Could not register with %s", cls.__name__, exc_info=True)
