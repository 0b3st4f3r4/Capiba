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
from capiba.ingestion.cnpj import partner_key
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

_PARTNER_DOC_FIELDS = [
    "cnpj_basico",
    "identificador",
    "nome",
    "qualificacao",
    "data_entrada",
    "faixa_etaria",
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


def bulk_upsert_cnpj(
    db: StandardDatabase,
    companies: Iterable[dict[str, Any]],
    partners: Iterable[dict[str, Any]],
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Persists CNPJ entities (silver rows) in the ArangoDB graph in bulk.

    Vertex keys: companies are keyed by ``cnpj_basico``; partners by their
    ``partner_id`` (hash of company + name + qualification — the masked
    ``cnpj_cpf_socio`` is never a key). Every partner also yields a
    ``partner_of`` edge pointing at its company.

    Args:
        db: Connection to the Capiba database.
        companies: Silver ``companies`` rows.
        partners: Silver ``partners`` rows.
        batch_size: Documents per ``import_bulk`` call.

    Returns:
        Summary ``{companies, partners, edges, errors}``.
    """
    summary = {"companies": 0, "partners": 0, "edges": 0, "errors": 0}

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
                **{f: _json_safe(company.get(f)) for f in _COMPANY_DOC_FIELDS},
            }
            for company in batch
        ]
        _import("companies", docs, "companies")

    for batch in _batches(partners, batch_size):
        partner_docs: list[dict[str, Any]] = []
        edge_docs: list[dict[str, Any]] = []
        for partner in batch:
            partner_id = str(
                partner.get("partner_id")
                or partner_key(
                    str(partner.get("cnpj_basico") or ""),
                    partner.get("nome"),
                    partner.get("qualificacao"),
                )
            )
            partner_docs.append(
                {
                    "_key": partner_id,
                    "partner_id": partner_id,
                    **{f: _json_safe(partner.get(f)) for f in _PARTNER_DOC_FIELDS},
                }
            )
            company_key = partner.get("cnpj_basico")
            if company_key:
                company_key = _sanitize_key(str(company_key))
                edge_docs.append(
                    {
                        "_key": f"partners_{partner_id}__companies_{company_key}",
                        "_from": f"partners/{partner_id}",
                        "_to": f"companies/{company_key}",
                        "qualificacao": partner.get("qualificacao"),
                        "data_entrada": _json_safe(partner.get("data_entrada")),
                    }
                )
        _import("partners", partner_docs, "partners")
        _import("partner_of", edge_docs, "edges")

    logger.info("CNPJ persistence finished: %s", summary)
    return summary


def _schema_hash(contract: Contract) -> str:
    """Generates a hash of the contract schema for traceability."""
    schema_text = ",".join(Contract.model_fields.keys())
    return hashlib.sha256(schema_text.encode()).hexdigest()
