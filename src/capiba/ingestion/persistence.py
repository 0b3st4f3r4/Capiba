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
from typing import Any

from arango.database import StandardDatabase

from capiba.db.arangodb import upsert_edge, upsert_vertex
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


def _schema_hash(contract: Contract) -> str:
    """Generates a hash of the contract schema for traceability."""
    schema_text = ",".join(Contract.model_fields.keys())
    return hashlib.sha256(schema_text.encode()).hexdigest()
