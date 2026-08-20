"""BDD step definitions for the FollowTheMoney graph vocabulary (O4).

Feature file: tests/bdd/features/graph_ftm.feature
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.db.ftm import export_ftm_entities
from capiba.detection.graphs import partners_of_buyer

scenarios("features/graph_ftm.feature")


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (graph rows in, operator outputs out)."""
    return {}


@given(
    parsers.parse(
        'the supplier "{supplier}" of buyer "{buyer}" has partner "{name}" '
        'via "{edge}"'
    )
)
def supplier_with_partner(
    context: dict[str, Any], supplier: str, buyer: str, name: str, edge: str
) -> None:
    """Traversal rows of partners_of_buyer for the given supplier/partner."""
    context["buyer"] = buyer
    context["partner_rows"] = [
        {
            "supplier_cnpj": supplier,
            "company": supplier[:8],
            "edge": edge,
            "partner_key": "p1",
            "partner_schema": "Person",
            "partner_name": name,
        }
    ]


@given(
    parsers.parse(
        'the company "{cnpj_basico}" named "{name}" has partner '
        '"{partner}" via "{edge}"'
    )
)
def company_with_partner(
    context: dict[str, Any], cnpj_basico: str, name: str, partner: str, edge: str
) -> None:
    """Subgraph row of export_ftm_entities for the given company/partner."""
    context["ftm_subgraph"] = {
        "company": {
            "_id": f"companies/{cnpj_basico}",
            "_key": cnpj_basico,
            "schema": "Company",
            "cnpj_basico": cnpj_basico,
            "razao_social": name,
        },
        "inbound": [
            {
                "vertex": {
                    "_id": "persons/p1",
                    "_key": "p1",
                    "schema": "Person",
                    "nome": partner,
                },
                "edge": {
                    "_id": f"{edge}/persons_p1__companies_{cnpj_basico}",
                    "_key": f"persons_p1__companies_{cnpj_basico}",
                    "_from": "persons/p1",
                    "_to": f"companies/{cnpj_basico}",
                    "schema": "Ownership",
                },
            }
        ],
        "outbound": [],
    }


@when(parsers.parse('the partners of buyer "{buyer}" are requested'))
def request_partners(context: dict[str, Any], buyer: str) -> None:
    with (
        MagicMock() as db,
        _patch_aql("capiba.detection.graphs", context.get("partner_rows", [])),
    ):
        context["partners"] = partners_of_buyer(buyer, db=db)


@when(parsers.parse('the FtM export of "{cnpj}" is requested'))
def request_ftm_export(context: dict[str, Any], cnpj: str) -> None:
    subgraph = context.get("ftm_subgraph")
    rows = [subgraph] if subgraph else []
    with MagicMock() as db, _patch_aql("capiba.db.ftm", rows):
        context["ftm_entities"] = export_ftm_entities(cnpj, db=db)


def _patch_aql(module: str, rows: list) -> Any:
    """Patches execute_aql of the given module returning the given rows."""
    from unittest.mock import patch

    return patch(f"{module}.execute_aql", MagicMock(return_value=rows))


@then(
    parsers.parse(
        'the partner list includes "{name}" for supplier "{supplier}"'
    )
)
def partner_included(context: dict[str, Any], name: str, supplier: str) -> None:
    matches = [
        row
        for row in context["partners"]
        if row["partner_name"] == name and row["supplier_cnpj"] == supplier
    ]
    assert matches, f"partner {name} of supplier {supplier} not listed"


@then(parsers.parse('the FtM entities include a "{schema}" named "{name}"'))
def ftm_entity_named(context: dict[str, Any], schema: str, name: str) -> None:
    matches = [
        entity
        for entity in context["ftm_entities"]
        if entity["schema"] == schema and entity["properties"].get("name") == [name]
    ]
    assert matches, f"FtM {schema} named {name} not exported"


@then(
    parsers.parse(
        'the FtM entities include an "{schema}" from "{source}" to "{target}"'
    )
)
def ftm_edge_exported(
    context: dict[str, Any], schema: str, source: str, target: str
) -> None:
    role = {"Ownership": "owner", "Directorship": "director"}[schema]
    asset = {"Ownership": "asset", "Directorship": "organization"}[schema]
    matches = [
        entity
        for entity in context["ftm_entities"]
        if entity["schema"] == schema
        and entity["properties"].get(role) == [source]
        and entity["properties"].get(asset) == [target]
    ]
    assert matches, f"FtM {schema} {source} -> {target} not exported"
