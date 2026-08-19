"""Normalization of the Portal da Transparência sanction lists (CEIS/CNEP).

Chunk: sanctions
Responsibility: Validate the raw records of the CEIS (inidôneas/suspensas)
and CNEP (empresas punidas) endpoints into the unified ``Sanction`` entity
consumed by the silver ``sanctions`` lake table.

The parsing is defensive, mirroring the other crawlers/normalizers: dates
arrive as DD/MM/YYYY (ISO is also accepted), the fine amount as a
Brazilian decimal ("1.234,56") and nested payloads (``sancionado``,
``orgaoSancionador``, ``tipoSancao``, ``fundamentacao``) may be missing or
null — in those cases the fields become None instead of aborting the
record. Fields follow the documented CeisDTO/CnepDTO of the API.

Dependencies: pydantic
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SanctionList = Literal["ceis", "cnep"]


class Sanction(BaseModel):
    """A sanction record of the CEIS/CNEP lists (silver ``sanctions`` table)."""

    id: str
    list_name: SanctionList
    cnpj: str | None = None
    cpf: str | None = None
    sanctioned_name: str | None = None
    uf: str | None = None
    sanctioning_body: str | None = None
    sanction_type: str | None = None
    legal_basis: str | None = None
    process_number: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    publication_date: date | None = None
    fine_amount: Decimal | None = None

    @classmethod
    def from_ceis(cls, raw: dict[str, Any]) -> Sanction:
        """Normalizes a raw CEIS (inidôneas/suspensas) record."""
        return cls.from_transparency(raw, list_name="ceis")

    @classmethod
    def from_cnep(cls, raw: dict[str, Any]) -> Sanction:
        """Normalizes a raw CNEP (empresas punidas) record."""
        return cls.from_transparency(raw, list_name="cnep")

    @classmethod
    def from_transparency(
        cls, raw: dict[str, Any], list_name: SanctionList
    ) -> Sanction:
        """Normalizes a raw CEIS/CNEP record (the two payloads share a shape).

        Args:
            raw: Raw record as returned by ``GET /ceis`` or ``GET /cnep``.
            list_name: Which list the record came from.

        Returns:
            The validated ``Sanction``.
        """
        sancionado = raw.get("sancionado") or {}
        pessoa = raw.get("pessoa") or {}
        orgao = raw.get("orgaoSancionador") or {}
        tipo = raw.get("tipoSancao") or {}
        fundamentacao = raw.get("fundamentacao") or []

        cnpj, cpf = _split_document(
            sancionado.get("codigoFormatado")
            or pessoa.get("cnpjFormatado")
            or pessoa.get("cpfFormatado")
        )
        name = (
            sancionado.get("nome")
            or pessoa.get("razaoSocialReceita")
            or pessoa.get("nome")
        )
        legal_basis = "; ".join(
            str(item["descricao"])
            for item in fundamentacao
            if isinstance(item, dict) and item.get("descricao")
        )

        api_id = raw.get("id")
        record_id = (
            f"{list_name}-{api_id}"
            if api_id is not None
            else f"{list_name}-{cnpj or cpf or name or 'unknown'}"
        )

        return cls(
            id=record_id,
            list_name=list_name,
            cnpj=cnpj,
            cpf=cpf,
            sanctioned_name=name,
            uf=orgao.get("siglaUf"),
            sanctioning_body=orgao.get("nome"),
            sanction_type=tipo.get("descricaoPortal") or tipo.get("descricaoResumida"),
            legal_basis=legal_basis or None,
            process_number=raw.get("numeroProcesso"),
            start_date=_parse_api_date(raw.get("dataInicioSancao")),
            end_date=_parse_api_date(raw.get("dataFimSancao")),
            publication_date=_parse_api_date(raw.get("dataPublicacaoSancao")),
            fine_amount=_parse_brazilian_decimal(raw.get("valorMulta")),
        )


def _parse_api_date(value: Any) -> date | None:
    """Parses a DD/MM/YYYY API date (ISO is also accepted); empty -> None."""
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return datetime.strptime(text, "%d/%m/%Y").date()  # noqa: DTZ007
    logger.warning("Unrecognized sanction date format: %r", text)
    return None


def _parse_brazilian_decimal(value: Any) -> Decimal | None:
    """Normalizes a Brazilian decimal ("1.234,56") to a Decimal; None if empty."""
    if value is None or isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        logger.warning("Unrecognized sanction fine amount: %r", value)
        return None


def _split_document(value: Any) -> tuple[str | None, str | None]:
    """Splits a formatted CNPJ/CPF into (cnpj, cpf) with digits only."""
    if value is None:
        return None, None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 14:
        return digits, None
    if len(digits) == 11:
        return None, digits
    return None, None
