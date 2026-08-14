"""BDD step definitions for contract validation.

Feature file: tests/bdd/features/validation.feature
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.pipeline.tasks import validate_contracts

scenarios("features/validation.feature")


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (contracts/errors in, report out)."""
    return {"normalization_errors": 0}


@given(parsers.parse("{count:d} normalized contracts where {dups:d} share the same id"))
def contracts_with_duplicates(context: dict[str, Any], count: int, dups: int) -> None:
    contracts = [{"id": f"C{i:03d}"} for i in range(count - dups + 1)]
    contracts += [{"id": contracts[0]["id"]} for _ in range(dups - 1)]
    context["contracts"] = contracts


@given(parsers.parse("{count:d} normalized contracts with unique ids"))
def contracts_unique(context: dict[str, Any], count: int) -> None:
    context["contracts"] = [{"id": f"C{i:03d}"} for i in range(count)]


@given(parsers.parse("{errors:d} normalization errors from the previous step"))
def normalization_errors(context: dict[str, Any], errors: int) -> None:
    context["normalization_errors"] = errors


@when("the contracts are validated")
def validate(context: dict[str, Any]) -> None:
    context["report"] = validate_contracts(
        context["contracts"], normalization_errors=context["normalization_errors"]
    )


@then("the report marks the batch as invalid")
def report_invalid(context: dict[str, Any]) -> None:
    assert context["report"]["valid"] is False


@then("the report marks the batch as valid")
def report_valid(context: dict[str, Any]) -> None:
    assert context["report"]["valid"] is True


@then(parsers.parse("the report counts {count:d} duplicated id"))
@then(parsers.parse("the report counts {count:d} duplicated ids"))
def report_duplicates(context: dict[str, Any], count: int) -> None:
    assert context["report"]["duplicates"] == count


@then(parsers.parse("the report counts {count:d} normalization errors"))
def report_errors(context: dict[str, Any], count: int) -> None:
    assert context["report"]["normalization_errors"] == count
