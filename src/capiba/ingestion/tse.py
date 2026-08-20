"""Streaming parser of the TSE campaign finance dump (prestação de contas).

Chunk: tse
Responsibility: Parse the ``receitas_candidatos_<year>_*.csv`` members of
the prestação de contas eleitorais ZIP into validated pydantic records,
chunked so the multi-GB consolidated file never materializes in memory.

Unlike the CNPJ dump, the TSE CSVs carry a header row (``sep=";"``,
latin1, comma decimal); the official column names of the receitas layout
are mapped below. When the ZIP holds both the consolidated ``_BRASIL``
member and per-UF members, only the consolidated one is parsed (the per-UF
rows are a subset of it). Rows are validated one by one — invalid rows
are counted and skipped with a warning, never aborting the file.

The donor document (``NR_CPF_CNPJ_DOADOR``) is complete at the source
(11/14 digits); it is kept in the silver for the deterministic match of
the ``political_connection`` signal — masking for publication is a gold
mart concern (PR-D-08 §2, LGPD).

Dependencies: pandas, pydantic
"""

from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

SILVER_ENTITY = "campaign_donations"

_DUMP_NAME_RE = re.compile(r"prestacao_de_contas_eleitorais_candidatos_(\d{4})\.zip$")
_RECEITAS_MEMBER_RE = re.compile(r"receitas_candidatos_(\d{4})_(.+)\.csv$")

# TSE receitas header -> silver field (only the columns the platform uses;
# the layout has ~50 columns). See docs/preregistrations/PR-D-08.md §4.
_TSE_COLUMN_MAP = {
    "SQ_PRESTADOR_CONTAS": "prestador_sequential",
    "SQ_RECEITA": "revenue_sequential",
    "DT_RECEITA": "donation_date",
    "VR_RECEITA": "amount",
    "DS_ORIGEM_RECEITA": "revenue_origin",
    "NR_CPF_CNPJ_DOADOR": "donor_document",
    "NM_DOADOR": "donor_name",
    "NM_DOADOR_RFB": "donor_name_rfb",
    "NR_CPF_CNPJ_DOADOR_ORIGINARIO": "donor_origin_document",
    "NM_DOADOR_ORIGINARIO": "donor_origin_name",
    "NM_DOADOR_ORIGINARIO_RFB": "donor_origin_name_rfb",
    "SQ_CANDIDATO": "candidate_sequential",
    "NM_CANDIDATO": "candidate_name",
    "SG_PARTIDO": "party",
    "DS_CARGO": "office",
    "NM_UE": "ue_name",
    "SG_UF": "uf",
}

_TSE_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
)


def entity_for_dump(filename: str) -> str | None:
    """Maps a TSE dump ZIP file name to its silver entity.

    Returns:
        ``campaign_donations`` for the candidates' prestação de contas
        dump, or None for anything else (party accounts, extratos, ...).
    """
    if _DUMP_NAME_RE.search(filename.lower()):
        return SILVER_ENTITY
    return None


def _clean_document(value: Any) -> str | None:
    """Strips punctuation from a CPF/CNPJ; non 11/14-digit values -> None."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) in (11, 14):
        return digits
    return None


def _parse_tse_date(value: Any) -> date | None:
    """Parses a DT_RECEITA value (ISO datetime or DD/MM/YYYY in the dumps)."""
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _TSE_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unparseable TSE date: {text!r}")


def _parse_brazilian_decimal(value: Any) -> str | None:
    """Normalizes a Brazilian decimal ("1.234,56") to a Decimal-able string."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return text


def donation_id(prestador_sequential: str | None, revenue_sequential: str | None) -> str:
    """Stable donation identifier: hash of the prestador + receita sequentials."""
    raw = f"{prestador_sequential or ''}|{revenue_sequential or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class CampaignDonation(BaseModel):
    """One row of a receitas_candidatos file (silver ``campaign_donations``)."""

    id: str
    election_year: int
    donor_document: str | None = None
    donor_name: str | None = None
    donor_origin_document: str | None = None
    donor_origin_name: str | None = None
    donation_date: date | None = None
    amount: Decimal | None = None
    revenue_origin: str | None = None
    candidate_sequential: str | None = None
    candidate_name: str | None = None
    party: str | None = None
    office: str | None = None
    ue_name: str | None = None
    uf: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reshape(cls, data: Any) -> Any:
        """Flattens the raw TSE columns and computes the donation id.

        Rows already in silver shape (no ``NR_CPF_CNPJ_DOADOR`` key) pass
        through unchanged, so silver rows revalidate cleanly.
        """
        if not isinstance(data, dict) or "NR_CPF_CNPJ_DOADOR" not in data:
            return data
        flat = {
            target: data.get(source)
            for source, target in _TSE_COLUMN_MAP.items()
            if source in data
        }
        # election_year is injected by the parser from the member file name.
        flat["election_year"] = data.get("election_year")
        # The RFB-validated name is the canonical one when present.
        flat["donor_name"] = flat.get("donor_name_rfb") or flat.get("donor_name")
        flat["donor_origin_name"] = flat.get("donor_origin_name_rfb") or flat.get(
            "donor_origin_name"
        )
        flat.pop("donor_name_rfb", None)
        flat.pop("donor_origin_name_rfb", None)
        flat["donor_document"] = _clean_document(flat.get("donor_document"))
        flat["donor_origin_document"] = _clean_document(
            flat.get("donor_origin_document")
        )
        prestador = flat.pop("prestador_sequential", None)
        receita = flat.pop("revenue_sequential", None)
        if prestador or receita:
            flat["id"] = donation_id(prestador, receita)
        else:
            # Fallback for rows without the sequentials: hash the payload.
            flat["id"] = hashlib.sha256(
                repr(sorted(flat.items())).encode()
            ).hexdigest()[:32]
        return flat

    @field_validator("donation_date", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> date | None:
        return _parse_tse_date(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: Any) -> str | None:
        return _parse_brazilian_decimal(value)


def _receitas_members(zf: zipfile.ZipFile) -> list[str]:
    """Receitas members to parse: the consolidated _BRASIL file when present.

    The dump ships both ``receitas_candidatos_<year>_BRASIL.csv`` and the
    per-UF files (a subset of it); parsing both would duplicate rows, so
    the consolidated member wins and the per-UF members are only a
    fallback for ZIPs without it.
    """
    members = [
        name
        for name in zf.namelist()
        if not name.endswith("/") and _RECEITAS_MEMBER_RE.match(Path(name).name.lower())
    ]
    brasil = [m for m in members if m.lower().endswith("_brasil.csv")]
    return sorted(brasil or members)


def parse_tse_zip(
    zip_path: Path, chunk_size: int = 50_000
) -> Iterator[tuple[str, list[dict[str, Any]], int]]:
    """Parses a TSE prestação de contas ZIP in chunks, validating rows.

    The receitas members are read straight from the ZIP (no extraction).
    Invalid rows are counted and skipped with a warning.

    Args:
        zip_path: Path of a prestacao_de_contas_eleitorais_candidatos ZIP.
        chunk_size: Rows per chunk (one yield per chunk).

    Yields:
        Tuples (entity, records, errors) per chunk; records are
        JSON-serializable dicts matching the silver entity schema.

    Raises:
        ValueError: If the file name maps to no known entity.
    """
    entity = entity_for_dump(zip_path.name)
    if entity is None:
        raise ValueError(f"Unrecognized TSE dump file: {zip_path.name}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in _receitas_members(zf):
            match = _RECEITAS_MEMBER_RE.match(Path(member).name.lower())
            if match is None:  # defensive: _receitas_members already filters
                continue
            election_year = int(match.group(1))
            with zf.open(member) as handle:
                chunks = pd.read_csv(
                    handle,
                    sep=";",
                    encoding="latin1",
                    dtype=str,
                    chunksize=chunk_size,
                    low_memory=False,
                )
                for chunk in chunks:
                    records: list[dict[str, Any]] = []
                    errors = 0
                    rows = chunk.astype(object).where(chunk.notna(), None)
                    for row in rows.to_dict("records"):
                        row["election_year"] = election_year
                        try:
                            records.append(
                                CampaignDonation.model_validate(row).model_dump(
                                    mode="json"
                                )
                            )
                        except Exception as exc:
                            errors += 1
                            logger.warning(
                                "Skipping invalid %s row in %s: %s",
                                entity,
                                zip_path.name,
                                exc,
                            )
                    yield entity, records, errors
