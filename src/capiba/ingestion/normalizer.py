"""Unified entity schema.

Chunk: normalizer
Responsibility: Normalize raw data from multiple sources
into a unified entity schema.

Dependencies: pydantic
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class Supplier(BaseModel):
    """Supplier entity (company or individual)."""

    cnpj: str | None = Field(None, pattern=r"^\d{14}$")
    cpf: str | None = Field(None, pattern=r"^\d{11}$")
    legal_name: str
    trade_name: str | None = None
    primary_cnae: str | None = None
    state: str | None = None
    city: str | None = None


class Buyer(BaseModel):
    """Buyer entity (government agency)."""

    siafi_code: str
    name: str
    government_level: str  # federal, state, municipal
    uf: str
    city: str | None = None


class Contract(BaseModel):
    """Public contract or bid."""

    id: str
    process_number: str
    subject: str
    amount: Decimal
    signature_date: date
    validity_start: date
    validity_end: date
    buyer: Buyer
    supplier: Supplier
    modality: str  # pregao, concorrencia, dispensa, etc.
    status: str  # in progress, completed, terminated

    @classmethod
    def from_pncp(cls, raw: dict[str, Any]) -> Contract:
        """Builds a Contract from raw PNCP data.

        Accepts records from both the /v1/contratacoes/publicacao
        and /v1/contratos endpoints, with defensive field extraction.
        """
        agency = raw.get("orgaoEntidade") or {}
        unit = raw.get("unidadeOrgao") or {}
        supplier_raw = raw.get("fornecedor") or {}

        control_number = (
            raw.get("numeroControlePNCP")
            or raw.get("numeroContratoEmpenho")
            or raw.get("sequencialCompra")
        )
        identifier = (
            str(control_number) if control_number else _generate_fallback_id(raw)
        )

        government_level = _normalize_government_level(agency.get("esferaId"))
        uf = (unit.get("ufSigla") or "")[:2].upper()
        city = unit.get("municipioNome")

        buyer = Buyer(
            siafi_code=str(unit.get("codigoUnidade") or agency.get("cnpj") or ""),
            name=unit.get("nomeUnidade")
            or agency.get("razaosocial")
            or "Agency not informed",
            government_level=government_level,
            uf=uf,
            city=city,
        )

        supplier = _extract_supplier(supplier_raw, raw)

        amount = _parse_decimal(
            raw.get("valorTotalHomologado")
            or raw.get("valorGlobal")
            or raw.get("valorInicialCompra")
        )
        if amount is None:
            amount = Decimal("0")

        publication_date = _parse_date(raw.get("dataPublicacaoPncp"))
        opening_date = _parse_date(raw.get("dataAberturaProposta"))
        signature_date = (
            _parse_date(raw.get("dataAssinatura"))
            or publication_date
            or opening_date
            or date.today()
        )

        validity_start = _parse_date(raw.get("dataVigenciaInicio")) or signature_date
        validity_end = _parse_date(raw.get("dataVigenciaFim")) or validity_start

        contract_type = raw.get("tipoContrato") or {}
        modality = (
            raw.get("modalidadeNome")
            or contract_type.get("nome")
            or _modality_by_code(raw.get("modalidadeId"))
            or "not_informed"
        )
        status = (
            raw.get("situacaoCompraNome")
            or _status_by_code(raw.get("situacaoCompraId"))
            or "published"
        )

        return cls(
            id=identifier,
            process_number=str(
                raw.get("processo")
                or raw.get("numeroContratoEmpenho")
                or raw.get("numeroCompra")
                or identifier
            ),
            subject=raw.get("objetoCompra")
            or raw.get("objetoContrato")
            or "Subject not informed",
            amount=amount,
            signature_date=signature_date,
            validity_start=validity_start,
            validity_end=validity_end,
            buyer=buyer,
            supplier=supplier,
            modality=modality.lower(),
            status=status.lower(),
        )

    @classmethod
    def from_transparency(cls, raw: dict[str, Any]) -> Contract:
        """Builds a Contract from Portal da Transparência data.

        Supports payloads from the purchases and contracts endpoints,
        with defensive mapping of naming variations.
        """
        agency = (
            raw.get("unidadeGestora")
            or raw.get("unidadeGestoraCompras")
            or raw.get("orgao")
            or raw.get("orgaoEntidade")
            or raw.get("orgaoContratante")
            or {}
        )
        linked_agency = agency.get("orgaoVinculado") or agency.get("orgaoMaximo") or {}
        supplier_raw = (
            raw.get("fornecedor")
            or raw.get("contratado")
            or raw.get("fornecedores", [{}])[0]
            or {}
        )

        identifier = str(
            raw.get("id")
            or raw.get("numeroContrato")
            or raw.get("numero")
            or _generate_fallback_id(raw)
        )

        siafi_code = str(
            agency.get("codigo")
            or agency.get("codigoSIAFI")
            or linked_agency.get("codigo")
            or linked_agency.get("codigoSIAFI")
            or ""
        )
        buyer = Buyer(
            siafi_code=siafi_code,
            name=agency.get("nome")
            or agency.get("razaoSocial")
            or "Agency not informed",
            government_level=_normalize_government_level(
                agency.get("descricaoPoder")
                or agency.get("esfera")
                or agency.get("esferaId")
            ),
            uf=(agency.get("ufSigla") or agency.get("uf") or "")[:2].upper(),
            city=agency.get("municipioNome") or agency.get("municipio"),
        )

        supplier = _extract_supplier(supplier_raw, raw)

        amount = _parse_decimal(
            raw.get("valorInicial")
            or raw.get("valorGlobal")
            or raw.get("valorInicialCompra")
            or raw.get("valor")
        )
        if amount is None:
            amount = Decimal("0")

        signature_date = (
            _parse_date(raw.get("dataAssinatura"))
            or _parse_date(raw.get("dataPublicacao"))
            or date.today()
        )
        validity_start = _parse_date(raw.get("dataVigenciaInicio")) or signature_date
        validity_end = _parse_date(raw.get("dataVigenciaFim")) or validity_start

        modality = str(
            raw.get("modalidade") or raw.get("modalidadeNome") or "not informed"
        )
        status = str(raw.get("situacao") or raw.get("situacaoContrato") or "completed")

        return cls(
            id=identifier,
            process_number=str(
                raw.get("numeroProcesso") or raw.get("numeroContrato") or identifier
            ),
            subject=raw.get("objeto")
            or raw.get("objetoContrato")
            or "Subject not informed",
            amount=amount,
            signature_date=signature_date,
            validity_start=validity_start,
            validity_end=validity_end,
            buyer=buyer,
            supplier=supplier,
            modality=modality.lower(),
            status=status.lower(),
        )


def _generate_fallback_id(raw: dict[str, Any]) -> str:
    """Generates a fallback ID from fields available in the dict."""
    parts = [
        str(raw.get("orgaoEntidade", {}).get("cnpj", "")),
        str(raw.get("anoCompra") or raw.get("anoContrato") or ""),
        str(raw.get("sequencialCompra") or raw.get("sequencial") or ""),
    ]
    return "-".join(p for p in parts if p) or "unknown"


def _extract_supplier(supplier_raw: dict[str, Any], raw: dict[str, Any]) -> Supplier:
    """Extracts a Supplier entity from dicts with field variations."""
    if not supplier_raw:
        # Tries common fields in flattened responses
        supplier_raw = {
            "cnpj": raw.get("niFornecedor")
            or raw.get("cnpjFornecedor")
            or raw.get("cnpjContratado"),
            "legal_name": raw.get("nomeRazaoSocialFornecedor")
            or raw.get("nomeFornecedor")
            or raw.get("razaoSocialContratado")
            or "Supplier not informed",
            "cpf": raw.get("cpfFornecedor"),
        }

    ni = str(
        supplier_raw.get("cnpj")
        or supplier_raw.get("cnpjFormatado")
        or supplier_raw.get("cpf")
        or supplier_raw.get("cpfFormatado")
        or supplier_raw.get("ni")
        or ""
    ).strip()
    ni_digits = re.sub(r"\D", "", ni)

    legal_name = (
        supplier_raw.get("legal_name")
        or supplier_raw.get("razaoSocial")
        or supplier_raw.get("nome")
        or supplier_raw.get("nomeRazaoSocial")
        or "Supplier not informed"
    )

    cnpj: str | None = None
    cpf: str | None = None
    if len(ni_digits) == 14:
        cnpj = ni_digits
    elif len(ni_digits) == 11:
        cpf = ni_digits

    return Supplier(
        cnpj=cnpj,
        cpf=cpf,
        legal_name=legal_name,
        state=supplier_raw.get("uf") or supplier_raw.get("state"),
        city=supplier_raw.get("municipio") or supplier_raw.get("city"),
    )


def _parse_decimal(value: Any) -> Decimal | None:
    """Safely converts a value to Decimal."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def _parse_date(value: Any) -> date | None:
    """Converts a date string to date, accepting multiple formats."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value

    text = str(value).strip()
    formats = ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d/%m/%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(text[: len(fmt.replace("%", "")) + 10], fmt).date()
        except ValueError:
            continue

    # Fallback: tries to extract yyyy-mm-dd from the start of the string
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return date.fromisoformat(match.group(1))
    return None


def _normalize_government_level(level: Any) -> str:
    """Normalizes a PNCP government level code to the unified schema."""
    mapping = {
        "F": "federal",
        "E": "state",
        "M": "municipal",
        "D": "district",
        "FEDERAL": "federal",
        "ESTADUAL": "state",
        "MUNICIPAL": "municipal",
        "DISTRITAL": "district",
    }
    key = str(level).strip().upper() if isinstance(level, str) else str(level)
    return mapping.get(key, "federal")


def _modality_by_code(code: Any) -> str:
    """Returns the modality name from the PNCP code."""
    mapping = {
        1: "leilao_eletronico",
        2: "dialogo_competitivo",
        3: "concurso",
        4: "concorrencia_eletronica",
        5: "concorrencia_presencial",
        6: "pregao_eletronico",
        7: "pregao_presencial",
        8: "dispensa",
        9: "inexigibilidade",
        10: "manifestacao_interesse",
        11: "pre_qualificacao",
        12: "credenciamento",
        13: "leilao_presencial",
    }
    return mapping.get(code, "not_informed") if code is not None else "not_informed"


def _status_by_code(code: Any) -> str | None:
    """Returns the status name from the PNCP code."""
    if code is None:
        return None
    mapping = {
        1: "published",
        2: "revoked",
        3: "annulled",
        4: "suspended",
    }
    return mapping.get(code)
