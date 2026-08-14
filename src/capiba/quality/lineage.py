"""Data lineage — origin traceability.

Chunk: lineage
Responsibility: Track the origin, transformations and
dependencies of each piece of data in the pipeline.

Dependencies: uuid
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LineageNode:
    """Node in the data lineage graph."""

    type: str  # source, transformation, dataset, model, api
    name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None


@dataclass
class LineageEdge:
    """Edge in the lineage graph (dependency)."""

    source: str  # id of the source node
    target: str  # id of the target node
    type: str  # input, output, depends_on, derived_from
    metadata: dict[str, Any] = field(default_factory=dict)


class LineageTracker:
    """Data lineage tracker.

    Builds a provenance graph for pipeline auditing
    and debugging.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, LineageNode] = {}
        self.edges: list[LineageEdge] = []

    def register_source(
        self,
        name: str,
        url: str,
        api_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Registers a data source.

        Args:
            name: Source identifier name.
            url: API or endpoint URL.
            api_type: API type (REST, GraphQL, etc.).
            metadata: Additional metadata.

        Returns:
            ID of the created node.
        """
        node = LineageNode(
            type="source",
            name=name,
            metadata={"url": url, "api_type": api_type, **(metadata or {})},
        )
        self.nodes[node.id] = node
        logger.info("Source registered: %s (%s)", name, node.id)
        return node.id

    def register_transformation(
        self,
        name: str,
        input_ids: list[str],
        code_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Registers a transformation in the pipeline.

        Args:
            name: Transformation name (e.g. normalize_pncp).
            input_ids: IDs of the input nodes.
            code_hash: SHA-256 hash of the transformation code.
            metadata: Additional metadata.

        Returns:
            ID of the created node.
        """
        node = LineageNode(
            type="transformation",
            name=name,
            content_hash=code_hash,
            metadata=metadata or {},
        )
        self.nodes[node.id] = node

        for input_id in input_ids:
            self.edges.append(
                LineageEdge(
                    source=input_id,
                    target=node.id,
                    type="input",
                )
            )

        logger.info("Transformation registered: %s (%s)", name, node.id)
        return node.id

    def register_dataset(
        self,
        name: str,
        schema_hash: str,
        row_count: int,
        input_ids: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Registers a resulting dataset.

        Args:
            name: Dataset name.
            schema_hash: Schema hash (columns and types).
            row_count: Number of records.
            input_ids: IDs of the input nodes.
            metadata: Additional metadata.

        Returns:
            ID of the created node.
        """
        node = LineageNode(
            type="dataset",
            name=name,
            content_hash=schema_hash,
            metadata={"row_count": row_count, **(metadata or {})},
        )
        self.nodes[node.id] = node

        for input_id in input_ids:
            self.edges.append(
                LineageEdge(
                    source=input_id,
                    target=node.id,
                    type="derived_from",
                )
            )

        logger.info("Dataset registered: %s (%s, %d rows)", name, node.id, row_count)
        return node.id

    def export(self) -> dict[str, Any]:
        """Exports the lineage graph as a dict.

        Returns:
            Dict with serialized nodes and edges.
        """
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "name": n.name,
                    "timestamp": n.timestamp,
                    "metadata": n.metadata,
                    "content_hash": n.content_hash,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "source": a.source,
                    "target": a.target,
                    "type": a.type,
                    "metadata": a.metadata,
                }
                for a in self.edges
            ],
        }
