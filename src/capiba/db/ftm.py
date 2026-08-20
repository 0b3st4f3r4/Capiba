"""FollowTheMoney JSON export of the graph.

Chunk: db
Responsibility: Serialize the subgraph around a company (itself, its
partners and its holdings) as FtM entities — ``{id, schema, properties}``
— for interoperability with the investigative ecosystem (OpenSanctions,
Aleph).

Dependencies: capiba.db.arangodb
"""

from __future__ import annotations

import logging
from typing import Any

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql, get_capiba_db

logger = logging.getLogger(__name__)

# FtM entity id prefix per ArangoDB collection.
_COLLECTION_PREFIX = {"companies": "company", "persons": "person"}


def _entity_id(reference: str) -> str:
    """Builds the FtM entity id from an ArangoDB document reference."""
    collection, key = reference.split("/", 1)
    return f"{_COLLECTION_PREFIX[collection]}-{key}"


def _vertex_to_ftm(doc: dict[str, Any]) -> dict[str, Any]:
    """Converts a companies/persons vertex to an FtM Company/Person."""
    entity_id = _entity_id(doc["_id"])
    if doc["_id"].startswith("companies/"):
        properties: dict[str, list[Any]] = {}
        if doc.get("razao_social"):
            properties["name"] = [doc["razao_social"]]
        if doc.get("cnpj_basico"):
            properties["registrationNumber"] = [doc["cnpj_basico"]]
        return {"id": entity_id, "schema": "Company", "properties": properties}
    properties = {}
    if doc.get("nome"):
        properties["name"] = [doc["nome"]]
    if doc.get("cnpj_cpf_socio"):
        # Usually masked (``***...``) in the public dump.
        properties["idNumber"] = [doc["cnpj_cpf_socio"]]
    if doc.get("pais"):
        properties["nationality"] = [doc["pais"]]
    return {"id": entity_id, "schema": "Person", "properties": properties}


def _edge_to_ftm(edge: dict[str, Any]) -> dict[str, Any]:
    """Converts an ownership/directorship edge to its FtM entity."""
    collection = edge["_id"].split("/")[0]
    source = _entity_id(edge["_from"])
    target = _entity_id(edge["_to"])
    if collection == "ownership":
        schema = "Ownership"
        properties: dict[str, list[Any]] = {"owner": [source], "asset": [target]}
    else:
        schema = "Directorship"
        properties = {"director": [source], "organization": [target]}
    if edge.get("qualificacao"):
        properties["role"] = [edge["qualificacao"]]
    if edge.get("data_entrada"):
        properties["startDate"] = [edge["data_entrada"]]
    return {
        "id": f"{collection}-{edge['_key']}",
        "schema": schema,
        "properties": properties,
    }


def export_ftm_entities(
    cnpj: str, db: StandardDatabase | None = None
) -> list[dict[str, Any]]:
    """Exports the subgraph around a CNPJ as FtM JSON entities.

    The export covers the company itself, its partners (INBOUND
    ``ownership``/``directorship``) and its holdings (OUTBOUND
    ``ownership``), with the edges as FtM Ownership/Directorship
    entities. A 14-digit CNPJ is normalized to its ``cnpj_basico``.

    Args:
        cnpj: Company CNPJ (unformatted, 8 or 14 digits).
        db: ArangoDB connection. If None, creates a new one.

    Returns:
        List of FtM entities (``{id, schema, properties}``), deduplicated
        and sorted by id; empty when the company is not in the graph.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        LET company = DOCUMENT(CONCAT("companies/", @cnpj))
        LET inbound = (
            FOR v, e IN 1..1 INBOUND company ownership, directorship
                RETURN {vertex: v, edge: e}
        )
        LET outbound = company == null ? [] : (
            FOR v, e IN 1..1 OUTBOUND company ownership
                RETURN {vertex: v, edge: e}
        )
        RETURN {company: company, inbound: inbound, outbound: outbound}
    """
    rows = execute_aql(db, query, {"cnpj": cnpj[:8]})
    if not rows or rows[0].get("company") is None:
        return []

    entities: dict[str, dict[str, Any]] = {}
    subgraph = rows[0]

    def _add_vertex(doc: dict[str, Any]) -> None:
        entity = _vertex_to_ftm(doc)
        entities.setdefault(entity["id"], entity)

    def _add_edge(edge: dict[str, Any]) -> None:
        entity = _edge_to_ftm(edge)
        entities.setdefault(entity["id"], entity)

    _add_vertex(subgraph["company"])
    for hop in [*(subgraph["inbound"] or []), *(subgraph["outbound"] or [])]:
        if hop.get("vertex"):
            _add_vertex(hop["vertex"])
        if hop.get("edge"):
            _add_edge(hop["edge"])

    logger.info("FtM export for %s: %d entities", cnpj[:8], len(entities))
    return sorted(entities.values(), key=lambda e: e["id"])
