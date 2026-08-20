"""Persistence of normalized contracts in ArangoDB.

Chunk: persistence
Responsibility: Save contracts, buyers and suppliers
in the Capiba graph and record data lineage.

Dependencies: python-arango, capiba.db.arangodb
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from arango.database import StandardDatabase

from capiba.db.arangodb import upsert_edge, upsert_vertex
from capiba.ingestion.cnpj import edge_kind_for_qualificacao, partner_key
from capiba.ingestion.normalizer import Contract
from capiba.quality.lineage import LineageTracker

logger = logging.getLogger(__name__)


def _sanitize_key(value: str) -> str:
    """Generates a valid ArangoDB _key from an identifier.

    Replaces invalid characters with '_' and truncates at 254 characters.
    Ensures the key is never empty.
    """
    key = re.sub(r"[^a-zA-Z0-9_\-:.]", "_", str(value))
    key = key.strip("_.-")
    if not key:
        key = "_"
    return key[:254]


def _serialize_contract(contract: Contract) -> dict[str, Any]:
    """Serializes a Contract into a JSON/ArangoDB-compatible dict."""
    return contract.model_dump(mode="json")


def upsert_contract(
    db: StandardDatabase,
    contract: Contract,
    tracker: LineageTracker | None = None,
) -> dict[str, Any]:
    """Persists a single contract and its relationships.

    Args:
        db: Connection to the Capiba database.
        contract: Normalized contract.
        tracker: Optional lineage tracker.

    Returns:
        Operation metadata (inserted/updated keys).
    """
    doc = _serialize_contract(contract)
    key = _sanitize_key(contract.id)
    doc["_key"] = key

    upsert_vertex(db, "contracts", key, doc)
    logger.debug("Contract persisted: %s", key)

    if contract.supplier.cnpj:
        supplier_key = _sanitize_key(contract.supplier.cnpj)
        upsert_vertex(
            db,
            "suppliers",
            supplier_key,
            {
                "cnpj": contract.supplier.cnpj,
                "legal_name": contract.supplier.legal_name,
                "trade_name": contract.supplier.trade_name,
                "primary_cnae": contract.supplier.primary_cnae,
                "state": contract.supplier.state,
                "city": contract.supplier.city,
            },
        )
        upsert_edge(
            db,
            "won",
            f"suppliers/{supplier_key}",
            f"contracts/{key}",
            {"amount": str(contract.amount)},
        )
        logger.debug("Edge won created: %s -> %s", supplier_key, key)

    buyer_key = _sanitize_key(contract.buyer.siafi_code)
    upsert_vertex(
        db,
        "buyers",
        buyer_key,
        {
            "siafi_code": contract.buyer.siafi_code,
            "name": contract.buyer.name,
            "government_level": contract.buyer.government_level,
            "uf": contract.buyer.uf,
            "city": contract.buyer.city,
        },
    )

    if tracker is not None:
        tracker.register_dataset(
            name=f"contract_{key}",
            schema_hash=_schema_hash(contract),
            row_count=1,
            input_ids=[],
            metadata={"source": "pncp/transparency", "id": contract.id},
        )

    return {"contract_key": key, "supplier_key": contract.supplier.cnpj}


def bulk_upsert_contracts(
    db: StandardDatabase,
    contracts: list[Contract],
    tracker: LineageTracker | None = None,
) -> dict[str, Any]:
    """Persists a list of contracts in bulk.

    Args:
        db: Connection to the Capiba database.
        contracts: List of normalized contracts.
        tracker: Optional lineage tracker.

    Returns:
        Operation summary.
    """
    total = len(contracts)
    succeeded = 0
    errors = 0

    for contract in contracts:
        try:
            upsert_contract(db, contract, tracker=tracker)
            succeeded += 1
        except Exception as exc:
            errors += 1
            logger.warning("Failed to persist contract %s: %s", contract.id, exc)

    logger.info(
        "Persistence finished: %d succeeded, %d errors (total %d)",
        succeeded,
        errors,
        total,
    )
    return {"total": total, "succeeded": succeeded, "errors": errors}


def _batches(
    items: Iterable[dict[str, Any]], batch_size: int
) -> Iterator[list[dict[str, Any]]]:
    """Splits an iterable into fixed-size lists."""
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


_COMPANY_DOC_FIELDS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo",
]

_PERSON_DOC_FIELDS = [
    "nome",
    "cnpj_cpf_socio",
    "faixa_etaria",
    "pais",
]


def _json_safe(value: Any) -> Any:
    """Converts typed silver values (Decimal, date) to JSON-serializable ones.

    ``import_bulk`` serializes documents with the standard JSON encoder;
    typed silver rows carry ``Decimal`` (``capital_social``) and ``date``
    (``data_entrada``) values, which made every companies/partners batch
    fail on the first full graph load (2026-08-20).
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _ftm_edge(
    collection: str,
    from_collection: str,
    from_key: str,
    to_key: str,
    qualificacao: Any,
    data_entrada: Any,
) -> dict[str, Any]:
    """Builds an FtM Ownership/Directorship edge document."""
    return {
        "_key": f"{from_collection}_{from_key}__companies_{to_key}",
        "_from": f"{from_collection}/{from_key}",
        "_to": f"companies/{to_key}",
        "schema": "Ownership" if collection == "ownership" else "Directorship",
        "qualificacao": qualificacao,
        "data_entrada": _json_safe(data_entrada),
    }


def bulk_upsert_cnpj(
    db: StandardDatabase,
    companies: Iterable[dict[str, Any]],
    partners: Iterable[dict[str, Any]],
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Persists CNPJ entities (silver rows) in the ArangoDB graph in bulk.

    FollowTheMoney vocabulary (O4): companies are FtM ``Company`` vertices
    keyed by ``cnpj_basico``; individual partners (``identificador`` 2/3)
    are FtM ``Person`` vertices keyed by ``partner_id`` (hash of company +
    name + qualification — the masked ``cnpj_cpf_socio`` is never a key).
    A corporate partner (``identificador`` 1) becomes a ``Company`` vertex
    keyed by its own CNPJ básico (so ``trace_ownership`` chains real
    company→company holdings) and always yields an ``ownership`` edge.
    Individual partners yield ``ownership`` and/or ``directorship`` edges
    according to the RFB qualification
    (``cnpj.edge_kind_for_qualificacao``); a named legal representative
    yields an extra ``Person`` vertex + ``directorship`` edge.

    Args:
        db: Connection to the Capiba database.
        companies: Silver ``companies`` rows.
        partners: Silver ``partners`` rows.
        batch_size: Documents per ``import_bulk`` call.

    Returns:
        Summary ``{companies, persons, edges, errors}``.
    """
    summary = {"companies": 0, "persons": 0, "edges": 0, "errors": 0}

    def _import(collection: str, docs: list[dict[str, Any]], counter: str) -> None:
        if not docs:
            return
        try:
            db.collection(collection).import_bulk(docs, on_duplicate="replace")
            summary[counter] += len(docs)
        except Exception as exc:
            summary["errors"] += 1
            logger.warning(
                "Failed to import %d documents into %s: %s", len(docs), collection, exc
            )

    for batch in _batches(companies, batch_size):
        docs = [
            {
                "_key": _sanitize_key(str(company["cnpj_basico"])),
                "schema": "Company",
                **{f: _json_safe(company.get(f)) for f in _COMPANY_DOC_FIELDS},
            }
            for company in batch
        ]
        _import("companies", docs, "companies")

    for batch in _batches(partners, batch_size):
        person_docs: list[dict[str, Any]] = []
        company_docs: list[dict[str, Any]] = []
        ownership_docs: list[dict[str, Any]] = []
        directorship_docs: list[dict[str, Any]] = []
        for partner in batch:
            cnpj_basico = str(partner.get("cnpj_basico") or "")
            if not cnpj_basico:
                continue
            company_key = _sanitize_key(cnpj_basico)
            qualificacao = partner.get("qualificacao")
            data_entrada = partner.get("data_entrada")
            document = str(partner.get("cnpj_cpf_socio") or "")

            if partner.get("identificador") == "1" and re.fullmatch(
                r"\d{14}", document
            ):
                # Corporate partner: Company vertex keyed by its own CNPJ
                # básico — real data for the ownership traversal.
                holder_key = _sanitize_key(document[:8])
                company_docs.append(
                    {
                        "_key": holder_key,
                        "schema": "Company",
                        "cnpj_basico": document[:8],
                        "razao_social": partner.get("nome"),
                    }
                )
                ownership_docs.append(
                    _ftm_edge(
                        "ownership",
                        "companies",
                        holder_key,
                        company_key,
                        qualificacao,
                        data_entrada,
                    )
                )
            else:
                partner_id = str(
                    partner.get("partner_id")
                    or partner_key(cnpj_basico, partner.get("nome"), qualificacao)
                )
                person_docs.append(
                    {
                        "_key": partner_id,
                        "partner_id": partner_id,
                        "schema": "Person",
                        **{f: _json_safe(partner.get(f)) for f in _PERSON_DOC_FIELDS},
                    }
                )
                kind = edge_kind_for_qualificacao(
                    None if qualificacao is None else str(qualificacao)
                )
                if kind in ("ownership", "both"):
                    ownership_docs.append(
                        _ftm_edge(
                            "ownership",
                            "persons",
                            partner_id,
                            company_key,
                            qualificacao,
                            data_entrada,
                        )
                    )
                if kind in ("directorship", "both"):
                    directorship_docs.append(
                        _ftm_edge(
                            "directorship",
                            "persons",
                            partner_id,
                            company_key,
                            qualificacao,
                            data_entrada,
                        )
                    )

            representative = partner.get("nome_representante")
            if representative:
                rep_key = partner_key(
                    cnpj_basico,
                    str(representative),
                    f"REP-{partner.get('qualificacao_representante_legal') or ''}",
                )
                person_docs.append(
                    {
                        "_key": rep_key,
                        "partner_id": rep_key,
                        "schema": "Person",
                        "nome": representative,
                        "cnpj_cpf_socio": _json_safe(
                            partner.get("representante_legal")
                        ),
                    }
                )
                directorship_docs.append(
                    _ftm_edge(
                        "directorship",
                        "persons",
                        rep_key,
                        company_key,
                        partner.get("qualificacao_representante_legal"),
                        None,
                    )
                )
        _import("companies", company_docs, "companies")
        _import("persons", person_docs, "persons")
        _import("ownership", ownership_docs, "edges")
        _import("directorship", directorship_docs, "edges")

    logger.info("CNPJ persistence finished: %s", summary)
    return summary


def _schema_hash(contract: Contract) -> str:
    """Generates a hash of the contract schema for traceability."""
    schema_text = ",".join(Contract.model_fields.keys())
    return hashlib.sha256(schema_text.encode()).hexdigest()
