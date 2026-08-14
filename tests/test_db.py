"""Tests for the db vertical slice.

Responsibility: Validate the ArangoDB, full-text search and vector store
connectors with all external clients mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from capiba.db import arangodb, search, vectors


@pytest.fixture
def mock_db() -> MagicMock:
    """Fixture: a mocked ArangoDB StandardDatabase."""
    return MagicMock(name="StandardDatabase")


class TestArangoDBConnector:
    """Tests for the base ArangoDB connector."""

    def test_get_arango_client_http(self) -> None:
        """Must build an HTTP client when TLS is disabled."""
        with (
            patch.object(arangodb, "ARANGODB_USE_TLS", False),
            patch.object(arangodb, "ArangoClient") as mock_client_cls,
        ):
            arangodb.get_arango_client()
            hosts = mock_client_cls.call_args.kwargs["hosts"]
            assert hosts.startswith("http://")

    def test_get_arango_client_https(self) -> None:
        """Must build an HTTPS client when TLS is enabled."""
        with (
            patch.object(arangodb, "ARANGODB_USE_TLS", True),
            patch.object(arangodb, "ArangoClient") as mock_client_cls,
        ):
            arangodb.get_arango_client()
            hosts = mock_client_cls.call_args.kwargs["hosts"]
            assert hosts.startswith("https://")

    def test_get_system_db(self) -> None:
        """Must connect to the _system database as root."""
        mock_client = MagicMock()
        with patch.object(arangodb, "get_arango_client", return_value=mock_client):
            result = arangodb.get_system_db()

        mock_client.db.assert_called_once_with(
            "_system",
            username="root",
            password=arangodb.ARANGODB_ROOT_PASSWORD,
        )
        assert result is mock_client.db.return_value

    def test_ensure_database_creates_when_missing(self) -> None:
        """Must create the Capiba database when it does not exist."""
        sys_db = MagicMock()
        sys_db.has_database.return_value = False
        client = MagicMock()
        with (
            patch.object(arangodb, "get_system_db", return_value=sys_db),
            patch.object(arangodb, "get_arango_client", return_value=client),
        ):
            result = arangodb.ensure_database()

        sys_db.create_database.assert_called_once_with(arangodb.ARANGODB_DATABASE)
        client.db.assert_called_once_with(
            arangodb.ARANGODB_DATABASE,
            username="root",
            password=arangodb.ARANGODB_ROOT_PASSWORD,
        )
        assert result is client.db.return_value

    def test_ensure_database_skips_when_present(self) -> None:
        """Must not create the database when it already exists."""
        sys_db = MagicMock()
        sys_db.has_database.return_value = True
        with (
            patch.object(arangodb, "get_system_db", return_value=sys_db),
            patch.object(arangodb, "get_arango_client", return_value=MagicMock()),
        ):
            arangodb.ensure_database()

        sys_db.create_database.assert_not_called()

    def test_ensure_collections_creates_missing(self, mock_db: MagicMock) -> None:
        """Must create vertex and edge collections that do not exist."""
        mock_db.has_collection.return_value = False

        arangodb.ensure_collections(mock_db)

        total = len(arangodb.VERTEX_COLLECTIONS) + len(arangodb.EDGE_COLLECTIONS)
        assert mock_db.create_collection.call_count == total
        edge_calls = [
            c for c in mock_db.create_collection.call_args_list if c.kwargs.get("edge")
        ]
        assert len(edge_calls) == len(arangodb.EDGE_COLLECTIONS)

    def test_ensure_collections_skips_existing(self, mock_db: MagicMock) -> None:
        """Must not create collections that already exist."""
        mock_db.has_collection.return_value = True

        arangodb.ensure_collections(mock_db)

        mock_db.create_collection.assert_not_called()

    def test_ensure_graph_returns_existing(self, mock_db: MagicMock) -> None:
        """Must return the existing graph without creating it."""
        mock_db.has_graph.return_value = True

        result = arangodb.ensure_graph(mock_db)

        mock_db.graph.assert_called_once_with(arangodb.ARANGODB_GRAPH_NAME)
        mock_db.create_graph.assert_not_called()
        assert result is mock_db.graph.return_value

    def test_ensure_graph_creates_when_missing(self, mock_db: MagicMock) -> None:
        """Must create the graph with edge definitions when missing."""
        mock_db.has_graph.return_value = False

        result = arangodb.ensure_graph(mock_db)

        mock_db.create_graph.assert_called_once()
        args, kwargs = mock_db.create_graph.call_args
        assert args[0] == arangodb.ARANGODB_GRAPH_NAME
        edge_definitions = kwargs["edge_definitions"]
        assert {d["edge_collection"] for d in edge_definitions} == {
            "participates",
            "won",
            "owns",
        }
        assert result is mock_db.create_graph.return_value

    def test_get_capiba_db_initializes_everything(self, mock_db: MagicMock) -> None:
        """Must ensure database, collections and graph."""
        with (
            patch.object(arangodb, "ensure_database", return_value=mock_db),
            patch.object(arangodb, "ensure_collections") as mock_collections,
            patch.object(arangodb, "ensure_graph") as mock_graph,
        ):
            result = arangodb.get_capiba_db()

        mock_collections.assert_called_once_with(mock_db)
        mock_graph.assert_called_once_with(mock_db)
        assert result is mock_db

    def test_upsert_vertex(self, mock_db: MagicMock) -> None:
        """Must insert a vertex document with overwrite."""
        mock_db.collection.return_value.insert.return_value = {"_key": "S1"}

        result = arangodb.upsert_vertex(mock_db, "suppliers", "S1", {"name": "ACME"})

        mock_db.collection.assert_called_once_with("suppliers")
        mock_db.collection.return_value.insert.assert_called_once_with(
            {"_key": "S1", "name": "ACME"}, overwrite=True
        )
        assert result == {"_key": "S1"}

    def test_upsert_edge(self, mock_db: MagicMock) -> None:
        """Must insert an edge with a deterministic key."""
        arangodb.upsert_edge(mock_db, "won", "suppliers/S1", "bids/L1", {"weight": 1})

        mock_db.collection.return_value.insert.assert_called_once_with(
            {
                "_key": "suppliers_S1__bids_L1",
                "_from": "suppliers/S1",
                "_to": "bids/L1",
                "weight": 1,
            },
            overwrite=True,
        )

    def test_upsert_edge_without_data(self, mock_db: MagicMock) -> None:
        """Must insert an edge without extra payload."""
        arangodb.upsert_edge(mock_db, "owns", "companies/E1", "companies/E2")

        doc = mock_db.collection.return_value.insert.call_args.args[0]
        assert doc == {
            "_key": "companies_E1__companies_E2",
            "_from": "companies/E1",
            "_to": "companies/E2",
        }

    def test_execute_aql(self, mock_db: MagicMock) -> None:
        """Must execute the AQL query and materialize the cursor."""
        mock_db.aql.execute.return_value = iter([{"a": 1}, {"a": 2}])

        result = arangodb.execute_aql(mock_db, "RETURN 1", {"x": 1})

        mock_db.aql.execute.assert_called_once_with("RETURN 1", bind_vars={"x": 1})
        assert result == [{"a": 1}, {"a": 2}]

    def test_execute_aql_default_bind_vars(self, mock_db: MagicMock) -> None:
        """Must default bind variables to an empty dict."""
        mock_db.aql.execute.return_value = iter([])

        arangodb.execute_aql(mock_db, "RETURN 1")

        mock_db.aql.execute.assert_called_once_with("RETURN 1", bind_vars={})


class TestFullTextSearch:
    """Tests for the ArangoSearch full-text search connector."""

    def test_ensure_search_collection_creates_missing(self, mock_db: MagicMock) -> None:
        """Must create the search collection when missing."""
        mock_db.has_collection.return_value = False

        search.ensure_search_collection(db=mock_db)

        mock_db.create_collection.assert_called_once_with(search.DEFAULT_COLLECTION)

    def test_ensure_search_collection_skips_existing(self, mock_db: MagicMock) -> None:
        """Must not create the collection when it already exists."""
        mock_db.has_collection.return_value = True

        search.ensure_search_collection(db=mock_db)

        mock_db.create_collection.assert_not_called()

    def test_ensure_search_collection_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(search, "get_capiba_db") as mock_get_db:
            search.ensure_search_collection(db=None)

        mock_get_db.assert_called_once_with()

    def test_ensure_search_view_creates_missing(self, mock_db: MagicMock) -> None:
        """Must create the ArangoSearch view when missing."""
        mock_db.has_collection.return_value = True
        mock_db.views.return_value = []

        search.ensure_search_view(db=mock_db)

        mock_db.create_arangosearch_view.assert_called_once()
        _, kwargs = mock_db.create_arangosearch_view.call_args
        assert kwargs["name"] == search.DEFAULT_VIEW
        assert search.DEFAULT_COLLECTION in kwargs["properties"]["links"]

    def test_ensure_search_view_skips_existing(self, mock_db: MagicMock) -> None:
        """Must not recreate a view that already exists."""
        mock_db.has_collection.return_value = True
        mock_db.views.return_value = [{"name": search.DEFAULT_VIEW}]

        search.ensure_search_view(db=mock_db)

        mock_db.create_arangosearch_view.assert_not_called()

    def test_ensure_search_view_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(search, "get_capiba_db") as mock_get_db:
            search.ensure_search_view(db=None)

        mock_get_db.assert_called_once_with()

    def test_index_document(self, mock_db: MagicMock) -> None:
        """Must upsert the document with default fields."""
        mock_db.has_collection.return_value = True
        mock_db.collection.return_value.insert.return_value = {"_key": "doc1"}

        result = search.index_document("doc1", "some content", db=mock_db)

        mock_db.collection.return_value.insert.assert_called_once_with(
            {
                "_key": "doc1",
                "external_id": "doc1",
                "content": "some content",
                "title": "",
                "tags": [],
                "payload": {},
            },
            overwrite=True,
        )
        assert result == {"_key": "doc1"}

    def test_index_document_with_metadata(self, mock_db: MagicMock) -> None:
        """Must keep the provided title, tags and payload."""
        mock_db.has_collection.return_value = True

        search.index_document(
            "doc2",
            "content",
            title="Title",
            tags=["a"],
            payload={"source": "pncp"},
            db=mock_db,
        )

        doc = mock_db.collection.return_value.insert.call_args.args[0]
        assert doc["title"] == "Title"
        assert doc["tags"] == ["a"]
        assert doc["payload"] == {"source": "pncp"}

    def test_index_document_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(search, "get_capiba_db") as mock_get_db:
            search.index_document("doc1", "content", db=None)

        mock_get_db.assert_called_once_with()

    def test_search_text(self, mock_db: MagicMock) -> None:
        """Must run the AQL search with the query and limit as bind vars."""
        mock_db.has_collection.return_value = True
        mock_db.views.return_value = [{"name": search.DEFAULT_VIEW}]
        expected = [{"external_id": "doc1", "score": 0.9}]
        with patch.object(search, "execute_aql", return_value=expected) as mock_aql:
            result = search.search_text("licitação", top_k=3, db=mock_db)

        aql, bind_vars = mock_aql.call_args.args[1:]
        assert search.DEFAULT_VIEW in aql
        assert bind_vars == {"query": "licitação", "topK": 3}
        assert result == expected

    def test_search_text_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with (
            patch.object(search, "get_capiba_db") as mock_get_db,
            patch.object(search, "execute_aql", return_value=[]),
        ):
            search.search_text("licitação", db=None)

        mock_get_db.assert_called_once_with()

    def test_delete_document_existing(self, mock_db: MagicMock) -> None:
        """Must delete the document and return True when it exists."""
        mock_db.collection.return_value.has.return_value = True

        assert search.delete_document("doc1", db=mock_db) is True
        mock_db.collection.return_value.delete.assert_called_once_with("doc1")

    def test_delete_document_missing(self, mock_db: MagicMock) -> None:
        """Must return False when the document does not exist."""
        mock_db.collection.return_value.has.return_value = False

        assert search.delete_document("missing", db=mock_db) is False
        mock_db.collection.return_value.delete.assert_not_called()

    def test_delete_document_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(search, "get_capiba_db") as mock_get_db:
            search.delete_document("doc1", db=None)

        mock_get_db.assert_called_once_with()


class TestVectorStore:
    """Tests for the AQL-based vector store connector."""

    def test_ensure_vector_collection_creates_missing(self, mock_db: MagicMock) -> None:
        """Must create the collection and the persistent indexes."""
        mock_db.has_collection.return_value = False
        mock_db.collection.return_value.indexes.return_value = []

        vectors.ensure_vector_collection(db=mock_db)

        mock_db.create_collection.assert_called_once_with(vectors.DEFAULT_COLLECTION)
        col = mock_db.collection.return_value
        assert col.add_persistent_index.call_count == 2
        fields = {
            tuple(c.kwargs["fields"]) for c in col.add_persistent_index.call_args_list
        }
        assert fields == {("collection_name",), ("external_id",)}

    def test_ensure_vector_collection_skips_existing_indexes(
        self, mock_db: MagicMock
    ) -> None:
        """Must not recreate collection or indexes that already exist."""
        mock_db.has_collection.return_value = True
        mock_db.collection.return_value.indexes.return_value = [
            {"fields": ["collection_name"]},
            {"fields": ["external_id"]},
        ]

        vectors.ensure_vector_collection(db=mock_db)

        mock_db.create_collection.assert_not_called()
        mock_db.collection.return_value.add_persistent_index.assert_not_called()

    def test_ensure_vector_collection_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(vectors, "get_capiba_db") as mock_get_db:
            vectors.ensure_vector_collection(db=None)

        mock_get_db.assert_called_once_with()

    def test_upsert_vector(self, mock_db: MagicMock) -> None:
        """Must upsert the vector with a composite key."""
        mock_db.has_collection.return_value = True
        mock_db.collection.return_value.indexes.return_value = [
            {"fields": ["collection_name"]},
            {"fields": ["external_id"]},
        ]
        mock_db.collection.return_value.insert.return_value = {"_key": "signals_v1"}

        result = vectors.upsert_vector(
            "v1", [1.0, 0.0], {"title": "Doc"}, collection_name="signals", db=mock_db
        )

        mock_db.collection.return_value.insert.assert_called_once_with(
            {
                "_key": "signals_v1",
                "external_id": "v1",
                "collection_name": "signals",
                "embedding": [1.0, 0.0],
                "payload": {"title": "Doc"},
            },
            overwrite=True,
        )
        assert result == {"_key": "signals_v1"}

    def test_upsert_vector_default_payload(self, mock_db: MagicMock) -> None:
        """Must default the payload to an empty dict."""
        mock_db.has_collection.return_value = True

        vectors.upsert_vector("v1", [1.0], db=mock_db)

        doc = mock_db.collection.return_value.insert.call_args.args[0]
        assert doc["payload"] == {}
        assert doc["_key"] == "default_v1"

    def test_upsert_vector_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(vectors, "get_capiba_db") as mock_get_db:
            vectors.upsert_vector("v1", [1.0], db=None)

        mock_get_db.assert_called_once_with()

    def test_search_similar_without_filter(self, mock_db: MagicMock) -> None:
        """Must search without a collection filter by default."""
        mock_db.has_collection.return_value = True
        expected = [{"external_id": "v1", "similarity": 0.99}]
        with patch.object(vectors, "execute_aql", return_value=expected) as mock_aql:
            result = vectors.search_similar([1.0, 0.0], top_k=2, db=mock_db)

        query, bind_vars = mock_aql.call_args.args[1:]
        assert "FILTER v.collection_name" not in query
        assert bind_vars == {"embedding": [1.0, 0.0], "topK": 2}
        assert result == expected

    def test_search_similar_with_collection_filter(self, mock_db: MagicMock) -> None:
        """Must add a filter and bind var when collection_name is given."""
        mock_db.has_collection.return_value = True
        with patch.object(vectors, "execute_aql", return_value=[]) as mock_aql:
            vectors.search_similar(
                [1.0, 0.0], top_k=5, collection_name="signals", db=mock_db
            )

        query, bind_vars = mock_aql.call_args.args[1:]
        assert "FILTER v.collection_name == @collectionName" in query
        assert bind_vars["collectionName"] == "signals"

    def test_search_similar_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with (
            patch.object(vectors, "get_capiba_db") as mock_get_db,
            patch.object(vectors, "execute_aql", return_value=[]),
        ):
            vectors.search_similar([1.0], db=None)

        mock_get_db.assert_called_once_with()

    def test_delete_vector_existing(self, mock_db: MagicMock) -> None:
        """Must delete the vector by composite key and return True."""
        mock_db.collection.return_value.has.return_value = True

        assert vectors.delete_vector("v1", collection_name="signals", db=mock_db)
        mock_db.collection.return_value.has.assert_called_once_with("signals_v1")
        mock_db.collection.return_value.delete.assert_called_once_with("signals_v1")

    def test_delete_vector_missing(self, mock_db: MagicMock) -> None:
        """Must return False when the vector does not exist."""
        mock_db.collection.return_value.has.return_value = False

        assert not vectors.delete_vector("missing", db=mock_db)
        mock_db.collection.return_value.delete.assert_not_called()

    def test_delete_vector_default_db(self) -> None:
        """Must create its own connection when db is not provided."""
        with patch.object(vectors, "get_capiba_db") as mock_get_db:
            vectors.delete_vector("v1", db=None)

        mock_get_db.assert_called_once_with()
