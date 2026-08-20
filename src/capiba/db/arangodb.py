"""ArangoDB connector for Capiba.

Responsibility: centralize the connection and common operations on ArangoDB,
including collection creation, graph, vertex/edge insertion and
traversal.

Dependencies: python-arango
"""

from __future__ import annotations

import logging
from typing import Any, cast

from arango.client import ArangoClient
from arango.cursor import Cursor
from arango.database import StandardDatabase
from arango.graph import Graph

from capiba.config import (
    ARANGODB_DATABASE,
    ARANGODB_GRAPH_NAME,
    ARANGODB_HOST,
    ARANGODB_PORT,
    ARANGODB_ROOT_PASSWORD,
    ARANGODB_USE_TLS,
)

logger = logging.getLogger(__name__)

VERTEX_COLLECTIONS = [
    "suppliers",
    "bids",
    "contracts",
    "buyers",
    "companies",
    "persons",
]
EDGE_COLLECTIONS = ["participates", "won", "ownership", "directorship", "same_as"]

# FtM vocabulary (O4): companies/persons are the FtM Company/Person
# vertices; ownership ({persons,companies} -> companies) and directorship
# (persons -> companies) are the FtM Ownership/Directorship edges. The
# legacy ``partners``/``partner_of``/``owns`` collections were renamed away
# (the graph data is reproducible from the silver layer).
EDGE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "edge_collection": "participates",
        "from_vertex_collections": ["suppliers"],
        "to_vertex_collections": ["bids"],
    },
    {
        "edge_collection": "won",
        "from_vertex_collections": ["suppliers"],
        "to_vertex_collections": ["bids", "contracts"],
    },
    {
        "edge_collection": "ownership",
        "from_vertex_collections": ["persons", "companies"],
        "to_vertex_collections": ["companies"],
    },
    {
        "edge_collection": "directorship",
        "from_vertex_collections": ["persons"],
        "to_vertex_collections": ["companies"],
    },
    {
        # Entity resolution (O5): computed hypothesis of identity between
        # person vertices, only above the pre-registered threshold.
        "edge_collection": "same_as",
        "from_vertex_collections": ["persons"],
        "to_vertex_collections": ["persons"],
    },
]


def get_arango_client() -> ArangoClient:
    """Returns a configured ArangoDB client."""
    protocol = "https" if ARANGODB_USE_TLS else "http"
    return ArangoClient(hosts=f"{protocol}://{ARANGODB_HOST}:{ARANGODB_PORT}")


def get_system_db() -> StandardDatabase:
    """Connects to the system database (_system) as root."""
    client = get_arango_client()
    return client.db(
        "_system",
        username="root",
        password=ARANGODB_ROOT_PASSWORD,
    )


def ensure_database() -> StandardDatabase:
    """Ensures the Capiba database exists and returns a connection."""
    sys_db = get_system_db()
    if not sys_db.has_database(ARANGODB_DATABASE):
        sys_db.create_database(ARANGODB_DATABASE)
        logger.info("ArangoDB database created: %s", ARANGODB_DATABASE)

    client = get_arango_client()
    return client.db(
        ARANGODB_DATABASE,
        username="root",
        password=ARANGODB_ROOT_PASSWORD,
    )


def ensure_collections(db: StandardDatabase) -> None:
    """Ensures the vertex and edge collections exist."""
    for name in VERTEX_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name)
            logger.info("Collection created: %s", name)

    for name in EDGE_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name, edge=True)
            logger.info("Edge collection created: %s", name)


def ensure_graph(db: StandardDatabase) -> Graph:
    """Ensures the Capiba graph exists, with every edge definition.

    Idempotent for graphs created by older versions: edge definitions
    missing from an existing graph are added in place (clusters created
    before the FtM vocabulary keep working).
    """
    if db.has_graph(ARANGODB_GRAPH_NAME):
        graph = db.graph(ARANGODB_GRAPH_NAME)
        for definition in EDGE_DEFINITIONS:
            if not graph.has_edge_definition(definition["edge_collection"]):
                graph.create_edge_definition(
                    edge_collection=definition["edge_collection"],
                    from_vertex_collections=definition["from_vertex_collections"],
                    to_vertex_collections=definition["to_vertex_collections"],
                )
                logger.info(
                    "Edge definition added: %s", definition["edge_collection"]
                )
        return graph

    graph = cast(
        Graph, db.create_graph(ARANGODB_GRAPH_NAME, edge_definitions=EDGE_DEFINITIONS)
    )
    logger.info("Graph created: %s", ARANGODB_GRAPH_NAME)
    return graph


def get_capiba_db() -> StandardDatabase:
    """Returns the Capiba database with collections/graph initialized."""
    db = ensure_database()
    ensure_collections(db)
    ensure_graph(db)
    return db


def upsert_vertex(
    db: StandardDatabase,
    collection: str,
    key: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Inserts or updates a vertex."""
    col = db.collection(collection)
    doc = {"_key": key, **data}
    return cast(dict[str, Any], col.insert(doc, overwrite=True))


def upsert_edge(
    db: StandardDatabase,
    collection: str,
    from_id: str,
    to_id: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inserts or updates an edge."""
    col = db.collection(collection)
    key = f"{from_id.replace('/', '_')}__{to_id.replace('/', '_')}"
    doc = {
        "_key": key,
        "_from": from_id,
        "_to": to_id,
        **(data or {}),
    }
    return cast(dict[str, Any], col.insert(doc, overwrite=True))


def execute_aql(
    db: StandardDatabase,
    query: str,
    bind_vars: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Executes an AQL query and returns the results."""
    cursor = cast(Cursor, db.aql.execute(query, bind_vars=bind_vars or {}))
    return list(cursor)
