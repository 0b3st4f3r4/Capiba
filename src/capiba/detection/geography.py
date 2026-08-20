"""Anomalous geography signal: distant supplier x buyer municipality.

Responsibility: emit the ``anomalous_geography`` signal when a corporate
supplier's municipality seat is farther than ``max_distance_km`` (strict
gate) from the buyer's municipality seat, under the semantics
pre-registered in ``docs/preregistrations/PR-D-09.md`` (section 3):

- **Coordinate resolution**: the buyer resolves by the normalized
  (``buyer.city``, ``buyer.uf``) pair — municipality names are unique per
  UF, so the mapping is deterministic; the corporate supplier resolves by
  ``supplier.cnpj`` -> silver ``establishments`` (matriz wins) -> TOM code
  -> ``rfb_municipalities`` name -> reference municipality. Any missing
  link means the pair has no coordinates and **never signals**.
- **Distance**: haversine with R = 6371.0 km between municipality seats,
  the declared formula
  ``2R·asin(√(sin²(Δφ/2) + cos φ1·cos φ2·sin²(Δλ/2)))``, degrees -> radians.
- **Distance gate**: signal iff ``distance_km > max_distance_km`` (strict;
  the pre-registered placeholder is 100.0 km).
- **Score**: ``round(min(1.0, distance_km / score_distance_reference), 4)``
  — saturates at 1.0 from the reference distance (1000.0 km).
- **Emission**: one signal per (supplier document, buyer municipality) —
  all contracts of the pair share the same distance; ``details`` carries
  the distance, the city names and IBGE codes of both municipalities, the
  contract count and total, and the gate used (so the signal can be
  recomputed from the silver rows, PR-D-09 section 6).
- **Individuals (CPF) never signal in v1**: the RFB dump carries no
  address for natural persons (declared limitation).

This module replaces the dead AQL operator ``graphs.anomalous_geography``
(removed in the same slice — it filtered ``bid`` vertices the graph never
creates). Thresholds live in the battery config
(``experiments/detect/D-09.json``), never only in code.

Dependencies: capiba.detection.political (city normalization), signals
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterable
from typing import Any

from capiba.detection.political import _amount, _normalize_city
from capiba.detection.signals import SignalType

logger = logging.getLogger(__name__)

# Pre-registered defaults (PR-D-09, section 3); the battery config carries
# the authoritative values.
MAX_DISTANCE_KM = 100.0
SCORE_DISTANCE_REFERENCE = 1000.0
EARTH_RADIUS_KM = 6371.0


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    radius_km: float = EARTH_RADIUS_KM,
) -> float:
    """Great-circle distance in km between two lat/long points (degrees).

    The declared haversine formula of PR-D-09 section 3, R = 6371.0 km.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _digits(value: Any) -> str:
    """Keeps only the digits of a value; empty means no value."""
    return re.sub(r"\D", "", str(value or ""))


def _municipality_index(
    municipalities: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Indexes the reference table by the normalized (name, UF) pair."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in municipalities:
        name = _normalize_city(row.get("name"))
        uf = str(row.get("uf") or "").strip().upper()
        if not name or not uf:
            continue
        index.setdefault(
            (name, uf),
            {
                "ibge_code": row.get("ibge_code"),
                "name": row.get("name"),
                "uf": uf,
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
            },
        )
    return index


def _supplier_index(
    establishments: Iterable[dict[str, Any]],
    rfb_municipalities: Iterable[dict[str, Any]],
    municipalities: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resolves each corporate supplier CNPJ to its municipality seat.

    The chain (PR-D-09 section 3): ``establishments.municipio`` (TOM code,
    matriz rows win over branches) -> ``rfb_municipalities`` name ->
    reference municipality coordinates. Any missing link leaves the CNPJ
    out of the index — the pair never signals.
    """
    tom_to_name = {
        _digits(row.get("tom_code")): str(row.get("name") or "")
        for row in rfb_municipalities
    }
    index: dict[str, dict[str, Any]] = {}
    for establishment in establishments:
        cnpj = _digits(establishment.get("cnpj"))
        if len(cnpj) != 14:
            continue
        if cnpj in index and not establishment.get("is_matriz"):
            continue
        name = tom_to_name.get(_digits(establishment.get("municipio")))
        if not name:
            continue
        uf = str(establishment.get("uf") or "").strip().upper()
        municipality = municipalities.get((_normalize_city(name), uf))
        if municipality is None:
            continue
        index[cnpj] = municipality
    return index


def _has_coordinates(municipality: dict[str, Any]) -> bool:
    """Whether the municipality entry carries both coordinates."""
    return (
        municipality.get("latitude") is not None
        and municipality.get("longitude") is not None
    )


def anomalous_geography_signals(
    contracts: Iterable[dict[str, Any]],
    establishments: Iterable[dict[str, Any]],
    rfb_municipalities: Iterable[dict[str, Any]],
    municipalities: Iterable[dict[str, Any]],
    max_distance_km: float = MAX_DISTANCE_KM,
    score_distance_reference: float = SCORE_DISTANCE_REFERENCE,
    earth_radius_km: float = EARTH_RADIUS_KM,
) -> list[dict[str, Any]]:
    """Emits one ``anomalous_geography`` signal per (supplier, buyer municipality).

    Args:
        contracts: Silver ``contracts`` rows (``supplier`` with optional
            cnpj/cpf, ``buyer`` with city/uf, ``amount``).
        establishments: Silver ``establishments`` rows (``cnpj``,
            ``municipio`` TOM code, ``uf``, ``is_matriz``).
        rfb_municipalities: Silver ``rfb_municipalities`` rows
            (``tom_code``, ``name``).
        municipalities: Reference municipality rows (``name``, ``uf``,
            ``ibge_code``, ``latitude``, ``longitude``) — the silver
            ``municipalities`` table in production, injected synthetically
            by the battery.
        max_distance_km: Strict distance gate (km).
        score_distance_reference: Distance (km) that saturates the score.
        earth_radius_km: Haversine earth radius (formula constant).

    Returns:
        One signal per (supplier document, buyer municipality) whose seat
        distance passes the strict gate; ``details`` carries the fields
        that ground the signal. Sorted by (entity id, buyer IBGE code) for
        bit-for-bit determinism.
    """
    mun_index = _municipality_index(municipalities)
    suppliers = _supplier_index(establishments, rfb_municipalities, mun_index)

    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        document = _digits(supplier.get("cnpj"))  # PF (cpf) never signals in v1
        if len(document) != 14:
            continue
        supplier_mun = suppliers.get(document)
        if supplier_mun is None or not _has_coordinates(supplier_mun):
            continue
        buyer = contract.get("buyer") or {}
        uf = str(buyer.get("uf") or "").strip().upper()
        buyer_mun = mun_index.get((_normalize_city(buyer.get("city")), uf))
        if buyer_mun is None or not _has_coordinates(buyer_mun):
            continue
        key = (document, str(buyer_mun.get("ibge_code") or ""))
        pair = pairs.setdefault(
            key,
            {
                "supplier_mun": supplier_mun,
                "buyer_mun": buyer_mun,
                "contracts": 0,
                "total": 0.0,
            },
        )
        pair["contracts"] += 1
        pair["total"] += _amount(contract.get("amount"))

    signals: list[dict[str, Any]] = []
    for (document, _ibge), pair in sorted(pairs.items()):
        supplier_mun = pair["supplier_mun"]
        buyer_mun = pair["buyer_mun"]
        distance = haversine_km(
            float(supplier_mun["latitude"]),
            float(supplier_mun["longitude"]),
            float(buyer_mun["latitude"]),
            float(buyer_mun["longitude"]),
            radius_km=earth_radius_km,
        )
        if not distance > max_distance_km:  # strict gate (PR-D-09 section 3)
            continue
        signals.append(
            {
                "entity_type": "supplier",
                "entity_id": document,
                "signal_type": SignalType.ANOMALOUS_GEOGRAPHY,
                "score": round(
                    min(1.0, distance / score_distance_reference), 4
                ),
                "details": json.dumps(
                    {
                        "buyer_city": buyer_mun.get("name"),
                        "buyer_ibge_code": buyer_mun.get("ibge_code"),
                        "buyer_uf": buyer_mun.get("uf"),
                        "contracts": pair["contracts"],
                        "contracts_total_brl": round(pair["total"], 2),
                        "distance_km": round(distance, 6),
                        "max_distance_km": max_distance_km,
                        "supplier_city": supplier_mun.get("name"),
                        "supplier_ibge_code": supplier_mun.get("ibge_code"),
                        "supplier_uf": supplier_mun.get("uf"),
                    },
                    sort_keys=True,
                ),
            }
        )
    return signals
