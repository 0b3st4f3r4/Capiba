"""Brazilian municipality geographic reference.

Responsibility: resolve Brazilian municipalities to their IBGE code and
lat/long coordinates, feeding the geographic enrichment of the graph
vertices (``buyers``/``suppliers`` in ``capiba.ingestion.persistence``) and
the silver ``municipalities`` reference table.

The data is the vendored ``data/municipios.csv`` (kelvins/Municipios-Brasileiros,
MIT — see ``data/README.md``), loaded lazily and indexed two ways:

- by the normalized (name, UF) pair — municipality names are unique per UF
  in the IBGE grid, so the buyer lookup from the PNCP payload
  (``unidadeOrgao.municipioNome`` + ``ufSigla``) is deterministic;
- by the 7-digit IBGE code.

All functions are pure and deterministic; the lookups are injectable so the
enrichment chains are testable offline.

Dependencies: pydantic
"""

from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Any

from pydantic import BaseModel, Field

# Vendored reference CSV inside the package (see reference/README.md for the
# license attribution). The directory is named ``reference`` and not ``data``
# because the root .gitignore excludes any ``data/`` directory (and hatchling
# honors .gitignore when building the wheel).
_MUNICIPIOS_RESOURCE = "reference/municipios.csv"

# IBGE state code (``codigo_uf`` column of the CSV, also the first two
# digits of the IBGE municipality code) -> UF sigla. The vendored CSV does
# not carry the sigla; the IBGE grid is stable and public.
UF_BY_IBGE_CODE = {
    "11": "RO",
    "12": "AC",
    "13": "AM",
    "14": "RR",
    "15": "PA",
    "16": "AP",
    "17": "TO",
    "21": "MA",
    "22": "PI",
    "23": "CE",
    "24": "RN",
    "25": "PB",
    "26": "PE",
    "27": "AL",
    "28": "SE",
    "29": "BA",
    "31": "MG",
    "32": "ES",
    "33": "RJ",
    "35": "SP",
    "41": "PR",
    "42": "SC",
    "43": "RS",
    "50": "MS",
    "51": "MT",
    "52": "GO",
    "53": "DF",
}


class Municipality(BaseModel):
    """One row of the vendored municipalities CSV (silver ``municipalities``)."""

    ibge_code: str = Field(pattern=r"^\d{7}$")
    name: str | None = None
    uf: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    siafi_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def normalize_municipality_name(name: Any) -> str:
    """Normalizes a municipality name: uppercase, no accents/punctuation.

    Same semantics as ``detection.political._normalize_city`` (kept as a
    separate copy so ingestion does not depend on the detection vertical):
    tokens are not sorted — city names compare as full strings.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Z0-9 ]", " ", text.upper())
    return " ".join(text.split())


def _digits(value: Any) -> str:
    """Keeps only the digits of a value; empty means no value."""
    return re.sub(r"\D", "", str(value or ""))


@lru_cache(maxsize=1)
def _municipalities() -> tuple[Municipality, ...]:
    """Loads and validates the vendored CSV exactly once per process."""
    resource = resources.files("capiba.ingestion").joinpath(_MUNICIPIOS_RESOURCE)
    rows: list[Municipality] = []
    with resource.open("r", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            uf = UF_BY_IBGE_CODE.get(str(raw.get("codigo_uf") or "").strip())
            rows.append(
                Municipality(
                    ibge_code=_digits(raw.get("codigo_ibge")),
                    name=(raw.get("nome") or "").strip() or None,
                    uf=uf,
                    siafi_code=_digits(raw.get("siafi_id")) or None,
                    latitude=float(raw["latitude"]),
                    longitude=float(raw["longitude"]),
                )
            )
    return tuple(rows)


@lru_cache(maxsize=1)
def _indexes() -> (
    tuple[dict[tuple[str, str], Municipality], dict[str, Municipality]]
):
    """Builds the (name, UF) and IBGE lookup indexes over the vendored CSV."""
    by_name: dict[tuple[str, str], Municipality] = {}
    by_ibge: dict[str, Municipality] = {}
    for municipality in _municipalities():
        by_ibge[municipality.ibge_code] = municipality
        if municipality.name and municipality.uf:
            key = (normalize_municipality_name(municipality.name), municipality.uf)
            by_name.setdefault(key, municipality)
    return by_name, by_ibge


def lookup_by_name(name: Any, uf: Any) -> Municipality | None:
    """Resolves a municipality by (name, UF), normalization-insensitive.

    Args:
        name: Municipality name in any case/accentuation (e.g. the PNCP
            ``unidadeOrgao.municipioNome``).
        uf: Two-letter UF sigla (e.g. ``ufSigla``).

    Returns:
        The municipality, or None when the pair is unknown.
    """
    sigla = str(uf or "").strip().upper()
    if not sigla:
        return None
    return _indexes()[0].get((normalize_municipality_name(name), sigla))


def lookup_by_ibge(ibge_code: Any) -> Municipality | None:
    """Resolves a municipality by its 7-digit IBGE code (digits only)."""
    return _indexes()[1].get(_digits(ibge_code))


def municipality_rows() -> list[dict[str, Any]]:
    """Returns the vendored municipalities as silver-ready dicts."""
    return [m.model_dump(mode="json") for m in _municipalities()]


def buyer_geo_fields(
    city: Any,
    uf: Any,
    lookup: Any = lookup_by_name,
) -> dict[str, Any] | None:
    """Resolves the geographic fields of a buyer vertex (city + UF).

    Args:
        city: Buyer municipality name.
        uf: Buyer UF sigla.
        lookup: (name, uf) -> Municipality resolver (injectable for tests).

    Returns:
        ``{"ibge_code", "latitude", "longitude"}`` or None when unresolved.
    """
    municipality = lookup(city, uf)
    if municipality is None:
        return None
    return {
        "ibge_code": municipality.ibge_code,
        "latitude": municipality.latitude,
        "longitude": municipality.longitude,
    }


def build_supplier_geo_index(
    establishments: Any,
    rfb_municipalities: Any,
    lookup: Any = lookup_by_name,
) -> dict[str, dict[str, Any]]:
    """Builds a CNPJ -> lat/long index from the silver RFB tables.

    The PNCP contract payload does not carry the supplier's municipality, so
    the chain goes through the Federal Revenue dump: the silver
    ``establishments`` row maps the CNPJ to a TOM municipality code
    (``municipio``), ``rfb_municipalities`` maps TOM to the official name
    and the vendored reference resolves (name, UF) to the coordinates.
    ``is_matriz`` rows win over branches of the same CNPJ.

    Args:
        establishments: Iterable of silver ``establishments`` rows (dicts
            with ``cnpj``, ``municipio``, ``uf``, ``is_matriz``).
        rfb_municipalities: Iterable of silver ``rfb_municipalities`` rows
            (dicts with ``tom_code``, ``name``).
        lookup: (name, uf) -> Municipality resolver (injectable for tests).

    Returns:
        Mapping of 14-digit CNPJ -> ``{"latitude", "longitude"}``.
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
        is_matriz = bool(establishment.get("is_matriz"))
        if cnpj in index and not is_matriz:
            continue
        name = tom_to_name.get(_digits(establishment.get("municipio")))
        if not name:
            continue
        municipality = lookup(name, establishment.get("uf"))
        if municipality is None:
            continue
        index[cnpj] = {
            "latitude": municipality.latitude,
            "longitude": municipality.longitude,
        }
    return index
