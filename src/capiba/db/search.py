"""Full-text search on ArangoDB via ArangoSearch.

Responsibility: index and search textual documents (evidence metadata,
tender notices, etc.) using ArangoSearch, replacing the role of
Elasticsearch in Capiba.

Dependencies: python-arango
"""

from __future__ import annotations

import logging
from typing import Any, cast

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql, get_capiba_db

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "search_documents"
DEFAULT_VIEW = "search_view"


def ensure_search_collection(
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> None:
    """Ensures the search documents collection exists."""
    if db is None:
        db = get_capiba_db()

    if not db.has_collection(collection):
        db.create_collection(collection)
        logger.info("Search collection created: %s", collection)


def ensure_search_view(
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
    view: str = DEFAULT_VIEW,
) -> None:
    """Ensures the ArangoSearch view exists and indexes the collection."""
    if db is None:
        db = get_capiba_db()

    ensure_search_collection(db, collection)

    existing_views = {v["name"] for v in cast(list[dict[str, Any]], db.views())}
    if view in existing_views:
        return

    db.create_arangosearch_view(
        name=view,
        properties={
            "links": {
                collection: {
                    "fields": {
                        "content": {"analyzers": ["text_pt"]},
                        "title": {"analyzers": ["text_pt"]},
                        "tags": {"analyzers": ["identity"]},
                    }
                }
            }
        },
    )
    logger.info("ArangoSearch view created: %s", view)


def index_document(
    external_id: str,
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> dict[str, Any]:
    """Indexes a document for full-text search.

    Args:
        external_id: External identifier.
        content: Text to be indexed.
        title: Optional title.
        tags: Optional tags.
        payload: Additional metadata.
        db: Connection to ArangoDB. If None, creates a new one.
        collection: Name of the documents collection.

    Returns:
        Result of the upsert operation.
    """
    if db is None:
        db = get_capiba_db()

    ensure_search_collection(db, collection)

    doc = {
        "_key": external_id,
        "external_id": external_id,
        "content": content,
        "title": title or "",
        "tags": tags or [],
        "payload": payload or {},
    }
    return cast(dict[str, Any], db.collection(collection).insert(doc, overwrite=True))


def search_text(
    query: str,
    top_k: int = 10,
    db: StandardDatabase | None = None,
    view: str = DEFAULT_VIEW,
    collection: str = DEFAULT_COLLECTION,
) -> list[dict[str, Any]]:
    """Searches documents by text using ArangoSearch.

    Args:
        query: Search term.
        top_k: Maximum number of results.
        db: Connection to ArangoDB. If None, creates a new one.
        view: Name of the ArangoSearch view.
        collection: Name of the underlying collection.

    Returns:
        List of documents ordered by relevance.
    """
    if db is None:
        db = get_capiba_db()

    ensure_search_view(db, collection, view)

    aql = f"""
        FOR doc IN {view}
            SEARCH ANALYZER(
                doc.content IN TOKENS(@query, "text_pt")
                OR doc.title IN TOKENS(@query, "text_pt"),
                "text_pt"
            )
            SORT TFIDF(doc) DESC
            LIMIT @topK
            RETURN {{
                external_id: doc.external_id,
                title: doc.title,
                score: TFIDF(doc),
                payload: doc.payload
            }}
    """
    return execute_aql(db, aql, {"query": query, "topK": top_k})


def delete_document(
    external_id: str,
    db: StandardDatabase | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> bool:
    """Removes a document from the search index.

    Args:
        external_id: External identifier.
        db: Connection to ArangoDB. If None, creates a new one.
        collection: Name of the documents collection.

    Returns:
        True if deleted, False otherwise.
    """
    if db is None:
        db = get_capiba_db()

    if db.collection(collection).has(external_id):
        db.collection(collection).delete(external_id)
        return True
    return False
