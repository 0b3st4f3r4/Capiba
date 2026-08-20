"""Streaming parser of the Federal Revenue CNPJ dump.

Chunk: federal_revenue
Responsibility: Parse the Empresas/Estabelecimentos/Socios/Municipios ZIP
members of the monthly CNPJ dump into validated pydantic records, chunked
so the multi-GB files never materialize in memory.

The dump layouts are positional (``header=None``, ``sep=";"``, latin1):
the official column order of each entity is declared below and rows are
validated one by one — invalid rows are counted and skipped with a
warning, never aborting the file.

Dependencies: pandas, pydantic
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Official column layouts of the CNPJ open data dump (RFB).
EMPRESAS_COLUMNS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo",
]

ESTABELECIMENTOS_COLUMNS = [
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "identificador_matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao_cadastral",
    "motivo_situacao_cadastral",
    "nome_cidade_exterior",
    "pais",
    "data_inicio_atividade",
    "cnae_fiscal_principal",
    "cnae_fiscal_secundaria",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd1",
    "telefone1",
    "ddd2",
    "telefone2",
    "ddd_fax",
    "fax",
    "correio_eletronico",
    "situacao_especial",
    "data_situacao_especial",
]

SOCIOS_COLUMNS = [
    "cnpj_basico",
    "identificador_socio",
    "nome_socio_razao_social",
    "cnpj_cpf_socio",
    "qualificacao_socio",
    "data_entrada_sociedade",
    "pais",
    "representante_legal",
    "nome_representante",
    "qualificacao_representante_legal",
    "faixa_etaria",
]

MUNICIPIOS_COLUMNS = [
    "tom_code",
    "nome",
]

# ZIP name prefix -> silver entity name. ``Municipios.zip`` is the small
# TOM code -> municipality name reference table shipped with the dump (the
# ``establishments.municipio`` column is a TOM code, not a name).
_ENTITY_PREFIXES = {
    "Empresas": "companies",
    "Estabelecimentos": "establishments",
    "Socios": "partners",
    "Municipios": "rfb_municipalities",
}


def _parse_rfb_date(value: Any) -> date | None:
    """Parses a YYYYMMDD dump date; "00000000"/empty values become None."""
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text == "00000000":
        return None
    if "-" in text:  # ISO date (e.g. a silver row being revalidated)
        return date.fromisoformat(text)
    return datetime.strptime(text, "%Y%m%d").date()


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


def partner_key(cnpj_basico: str, nome: str | None, qualificacao: str | None) -> str:
    """Stable partner identifier: hash of company + name + qualification.

    The ``cnpj_cpf_socio`` column is intentionally not used: it is usually
    masked (``***...``) in the public dump.
    """
    raw = f"{cnpj_basico}|{nome or ''}|{qualificacao or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# RFB qualification codes (Qualificacoes table of the dump): management
# roles map to an FtM Directorship edge, equity roles to FtM Ownership;
# the managing-partner codes carry both semantics.
DIRECTORSHIP_QUALIFICATIONS = frozenset(
    {
        "05",  # Administrador
        "08",  # Conselheiro de Administração
        "10",  # Diretor
        "16",  # Presidente
        "17",  # Procurador
    }
)
DUAL_QUALIFICATIONS = frozenset(
    {
        "28",  # Sócio-Gerente
        "49",  # Sócio-Administrador
    }
)


def edge_kind_for_qualificacao(qualificacao: str | None) -> str:
    """Classifies an RFB partner qualification as an FtM edge kind.

    Returns:
        ``"ownership"`` (equity), ``"directorship"`` (management) or
        ``"both"`` (managing partner). Unknown/missing codes default to
        ``"ownership"``, the common case in the dump.
    """
    code = (qualificacao or "").strip().zfill(2)
    if code in DUAL_QUALIFICATIONS:
        return "both"
    if code in DIRECTORSHIP_QUALIFICATIONS:
        return "directorship"
    return "ownership"


class Company(BaseModel):
    """One row of an Empresas* dump file (silver ``companies`` table)."""

    cnpj_basico: str = Field(pattern=r"^\d{8}$")
    razao_social: str | None = None
    natureza_juridica: str | None = None
    qualificacao_responsavel: str | None = None
    capital_social: Decimal | None = None
    porte_empresa: str | None = None
    ente_federativo: str | None = None

    @field_validator("capital_social", mode="before")
    @classmethod
    def _parse_capital(cls, value: Any) -> str | None:
        return _parse_brazilian_decimal(value)


class Establishment(BaseModel):
    """One row of an Estabelecimentos* file (silver ``establishments``)."""

    cnpj: str = Field(pattern=r"^\d{14}$")
    cnpj_basico: str = Field(pattern=r"^\d{8}$")
    is_matriz: bool = False
    nome_fantasia: str | None = None
    situacao_cadastral: str | None = None
    data_situacao_cadastral: date | None = None
    data_inicio_atividade: date | None = None
    cnae_principal: str | None = None
    uf: str | None = None
    municipio: str | None = None
    cep: str | None = None
    email: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reshape(cls, data: Any) -> Any:
        """Composes the full CNPJ and flattens the raw dump columns."""
        if not isinstance(data, dict) or "cnpj_ordem" not in data:
            return data
        data = dict(data)
        basico = str(data.get("cnpj_basico") or "")
        data["cnpj"] = (
            f"{basico}{data.get('cnpj_ordem') or ''}{data.get('cnpj_dv') or ''}"
        )
        data["is_matriz"] = str(data.get("identificador_matriz_filial") or "") == "1"
        data["cnae_principal"] = data.get("cnae_fiscal_principal")
        data["email"] = data.get("correio_eletronico")
        return data

    @field_validator("data_situacao_cadastral", "data_inicio_atividade", mode="before")
    @classmethod
    def _parse_dates(cls, value: Any) -> date | None:
        return _parse_rfb_date(value)


class Partner(BaseModel):
    """One row of a Socios* dump file (silver ``partners`` table)."""

    partner_id: str
    cnpj_basico: str = Field(pattern=r"^\d{8}$")
    identificador: str | None = None
    nome: str | None = None
    qualificacao: str | None = None
    data_entrada: date | None = None
    faixa_etaria: str | None = None
    cnpj_cpf_socio: str | None = None
    pais: str | None = None
    representante_legal: str | None = None
    nome_representante: str | None = None
    qualificacao_representante_legal: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reshape(cls, data: Any) -> Any:
        """Flattens the raw dump columns and computes the partner id."""
        if not isinstance(data, dict) or "nome_socio_razao_social" not in data:
            return data
        data = dict(data)
        data["identificador"] = data.get("identificador_socio")
        data["nome"] = data.get("nome_socio_razao_social")
        data["qualificacao"] = data.get("qualificacao_socio")
        data["data_entrada"] = data.get("data_entrada_sociedade")
        data["partner_id"] = partner_key(
            str(data.get("cnpj_basico") or ""),
            data["nome"],
            data["qualificacao"],
        )
        return data

    @field_validator("data_entrada", mode="before")
    @classmethod
    def _parse_dates(cls, value: Any) -> date | None:
        return _parse_rfb_date(value)


class RfbMunicipality(BaseModel):
    """One row of the Municipios* reference file (silver ``rfb_municipalities``).

    Maps the RFB/TOM municipality code — the value stored in the silver
    ``establishments.municipio`` column — to the official municipality name,
    the missing link of the supplier geo-enrichment chain (O6).
    """

    tom_code: str = Field(pattern=r"^\d{4}$")
    name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reshape(cls, data: Any) -> Any:
        """Flattens the raw dump columns (a revalidated silver row passes through)."""
        if not isinstance(data, dict) or "nome" not in data:
            return data
        data = dict(data)
        data["name"] = data.get("nome")
        return data


_ENTITY_MODELS: dict[str, type[BaseModel]] = {
    "companies": Company,
    "establishments": Establishment,
    "partners": Partner,
    "rfb_municipalities": RfbMunicipality,
}

_ENTITY_COLUMNS: dict[str, list[str]] = {
    "companies": EMPRESAS_COLUMNS,
    "establishments": ESTABELECIMENTOS_COLUMNS,
    "partners": SOCIOS_COLUMNS,
    "rfb_municipalities": MUNICIPIOS_COLUMNS,
}


def entity_for_zip(filename: str) -> str | None:
    """Maps a dump ZIP file name to its silver entity.

    Returns:
        ``companies``/``establishments``/``partners``/``rfb_municipalities``,
        or None for the non-entity files (Cnaes.zip, Motivos.zip, ...).
    """
    for prefix, entity in _ENTITY_PREFIXES.items():
        if filename.startswith(prefix):
            return entity
    return None


def parse_cnpj_zip(
    zip_path: Path, chunk_size: int = 50_000
) -> Iterator[tuple[str, list[dict[str, Any]], int]]:
    """Parses a CNPJ dump ZIP in chunks, validating rows one by one.

    The inner member is read straight from the ZIP (no extraction; any
    member extension is accepted — the real dumps use .EMPRECSV/.ESTABELE/
    .SOCIOCSV). Invalid rows are counted and skipped with a warning.

    Args:
        zip_path: Path of an Empresas*/Estabelecimentos*/Socios* ZIP.
        chunk_size: Rows per chunk (one yield per chunk).

    Yields:
        Tuples (entity, records, errors) per chunk; records are
        JSON-serializable dicts matching the silver entity schema.

    Raises:
        ValueError: If the file name maps to no known entity.
    """
    entity = entity_for_zip(zip_path.name)
    if entity is None:
        raise ValueError(f"Unrecognized CNPJ dump file: {zip_path.name}")

    model = _ENTITY_MODELS[entity]
    columns = _ENTITY_COLUMNS[entity]

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = [name for name in zf.namelist() if not name.endswith("/")]
        for member in members:
            with zf.open(member) as handle:
                chunks = pd.read_csv(
                    handle,
                    sep=";",
                    encoding="latin1",
                    header=None,
                    dtype=str,
                    chunksize=chunk_size,
                    low_memory=False,
                )
                for chunk in chunks:
                    chunk = chunk.iloc[:, : len(columns)]
                    chunk.columns = columns
                    records: list[dict[str, Any]] = []
                    errors = 0
                    rows = chunk.astype(object).where(chunk.notna(), None)
                    for row in rows.to_dict("records"):
                        try:
                            records.append(model.model_validate(row).model_dump(mode="json"))
                        except Exception as exc:
                            errors += 1
                            logger.warning(
                                "Skipping invalid %s row in %s: %s",
                                entity,
                                zip_path.name,
                                exc,
                            )
                    yield entity, records, errors
