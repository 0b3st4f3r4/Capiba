"""Vector store on ArangoDB for Capiba.

Responsibility: store embeddings and perform similarity search.
Uses AQL with cosine similarity, not requiring the experimental vector
index (--vector-index) of ArangoDB. For large volumes in the future,
one can migrate to native vector indexes.

Dependencies: python-arango
"""

from __future__ import annotations

import logging
from typing import Any, cast

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql, get_capiba_db

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "vectors"


def ensure_vector_collection(
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> None:
    """Ensures the vector collection exists with useful indexes."""
    if db is None:
        db = get_capiba_db()

    if not db.has_collection(collection):
        db.create_collection(collection)
        logger.info("Vector collection created: %s", collection)

    col = db.collection(collection)
    indexes = [idx["fields"] for idx in cast(list[dict[str, Any]], col.indexes())]
    if ["collection_name"] not in indexes:
        col.add_persistent_index(fields=["collection_name"], unique=False)
    if ["external_id"] not in indexes:
        col.add_persistent_index(fields=["external_id"], unique=False)


def upsert_vector(
    external_id: str,
    embedding: list[float],
    payload: dict[str, Any] | None = None,
    collection_name: str = "default",
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> dict[str, Any]:
    """Inserts or updates a vector in the collection.

    Args:
        external_id: External identifier of the document (e.g. tender notice hash).
        embedding: Embedding vector.
        payload: Additional metadata.
        collection_name: Logical grouping (e.g. signals, documents).
        db: Connection to ArangoDB. If None, creates a new one.
        collection: Name of the vector collection.

    Returns:
        Result of the upsert operation.
    """
    if db is None:
        db = get_capiba_db()

    ensure_vector_collection(db, collection)

    doc = {
        "_key": f"{collection_name}_{external_id}",
        "external_id": external_id,
        "collection_name": collection_name,
        "embedding": embedding,
        "payload": payload or {},
    }
    return cast(dict[str, Any], db.collection(collection).insert(doc, overwrite=True))


def search_similar(
    embedding: list[float],
    top_k: int = 5,
    collection_name: str | None = None,
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> list[dict[str, Any]]:
    """Searches for the most similar vectors using cosine similarity.

    Args:
        embedding: Query vector.
        top_k: Number of results.
        collection_name: Filter by logical grouping.
        db: Connection to ArangoDB. If None, creates a new one.
        collection: Name of the vector collection.

    Returns:
        List of documents ordered by descending similarity.
    """
    if db is None:
        db = get_capiba_db()

    ensure_vector_collection(db, collection)

    filter_clause = ""
    bind_vars: dict[str, Any] = {
        "embedding": embedding,
        "topK": top_k,
    }
    if collection_name:
        filter_clause = "FILTER v.collection_name == @collectionName"
        bind_vars["collectionName"] = collection_name

    query = f"""
        LET query_vec = @embedding
        FOR v IN {collection}
            {filter_clause}
            LET dot = SUM(
                FOR i IN 0..(LENGTH(query_vec) - 1)
                    RETURN query_vec[i] * v.embedding[i]
            )
            LET norm_query = SQRT(SUM(FOR x IN query_vec RETURN x * x))
            LET norm_doc = SQRT(SUM(FOR x IN v.embedding RETURN x * x))
            LET similarity = dot / (norm_query * norm_doc)
            FILTER similarity > 0
            SORT similarity DESC
            LIMIT @topK
            RETURN {{
                external_id: v.external_id,
                collection_name: v.collection_name,
                similarity: similarity,
                payload: v.payload
            }}
    """

    return execute_aql(db, query, bind_vars)


def delete_vector(
    external_id: str,
    collection_name: str = "default",
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> bool:
    """Removes a vector by its external identifier.

    Args:
        external_id: External identifier.
        collection_name: Logical grouping.
        db: Connection to ArangoDB. If None, creates a new one.
        collection: Name of the vector collection.

    Returns:
        True if deleted, False otherwise.
    """
    if db is None:
        db = get_capiba_db()

    key = f"{collection_name}_{external_id}"
    if db.collection(collection).has(key):
        db.collection(collection).delete(key)
        return True
    return False
