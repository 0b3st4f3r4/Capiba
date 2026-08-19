"""BDD step definitions for the portal editorial triage page.

Feature file: tests/bdd/features/portal_triage.feature
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from capiba import config
from capiba.api import portal, services
from capiba.api.main import app
from capiba.db import triage

scenarios("features/portal_triage.feature")

_FAKE_KEYCLOAK_ISSUER = "http://keycloak.capiba.local:8088/realms/capiba"
_FAKE_KEYCLOAK_METADATA: dict[str, Any] = {
    "issuer": _FAKE_KEYCLOAK_ISSUER,
    "authorization_endpoint": f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/auth",
    "token_endpoint": f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/token",
    "userinfo_endpoint": f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/userinfo",
}


class _FakeMetadataResponse:
    """Minimal httpx response stub for the OIDC metadata endpoint."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeCollection:
    """Dict-backed stand-in for an ArangoDB collection."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.docs.get(key)

    def insert(self, doc: dict[str, Any], silent: bool = False) -> dict[str, Any]:
        self.docs[doc["_key"]] = dict(doc)
        return dict(doc)

    def update(self, doc: dict[str, Any]) -> dict[str, Any]:
        self.docs[doc["_key"]].update(doc)
        return dict(self.docs[doc["_key"]])


class FakeDb:
    """Minimal ArangoDB stand-in wired to a single fake collection."""

    def __init__(self) -> None:
        self.col = FakeCollection()

    def has_collection(self, name: str) -> bool:
        return True

    def create_collection(self, name: str) -> None:
        pass

    def collection(self, name: str) -> FakeCollection:
        return self.col


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Shared scenario state: fake db wired to the portal read/write paths."""
    db = FakeDb()
    monkeypatch.setattr(services, "get_db", lambda: db)
    monkeypatch.setattr(
        triage,
        "execute_aql",
        lambda db_, query, bind_vars=None: list(db_.col.docs.values()),
    )
    return {"db": db, "client": TestClient(app)}


def _key(signal_type: str, cnpj: str) -> str:
    return triage.signal_key("supplier", cnpj, signal_type)


@given("que o SSO está desabilitado")
def sso_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SSO_ENABLED", False)


@given("que o SSO está habilitado")
def sso_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SSO_ENABLED", True)
    monkeypatch.setattr(config, "KEYCLOAK_ISSUER", _FAKE_KEYCLOAK_ISSUER)
    monkeypatch.setattr(
        httpx,
        "get",
        lambda _url, **_kwargs: _FakeMetadataResponse(_FAKE_KEYCLOAK_METADATA),
    )
    portal.register_keycloak(portal.oauth)


@given(
    parsers.parse(
        'um sinal "{signal_type}" do fornecedor "{cnpj}" aguardando revisão'
    )
)
def pending_signal(context: dict[str, Any], signal_type: str, cnpj: str) -> None:
    triage.register_signals(
        context["db"],
        [
            {
                "entity_type": "supplier",
                "entity_id": cnpj,
                "signal_type": signal_type,
                "score": 0.8,
                "details": '{"contracts": 4}',
            }
        ],
    )


@when("o usuário acessa a página de triagem")
def access_triage(context: dict[str, Any]) -> None:
    context["response"] = context["client"].get("/triage", follow_redirects=False)


@when(parsers.parse('a revisora "{reviewer}" confirma o sinal pela página'))
def confirm_signal(context: dict[str, Any], reviewer: str) -> None:
    context["response"] = context["client"].post(
        "/triage/review",
        data={
            "key": _key("single_bid", "12345678000199"),
            "status": "confirmed",
            "reviewer": reviewer,
            "filter": "pending_review",
        },
        follow_redirects=False,
    )


@when(parsers.parse('a revisora "{reviewer}" rejeita o sinal sem motivo pela página'))
def reject_without_reason(context: dict[str, Any], reviewer: str) -> None:
    context["response"] = context["client"].post(
        "/triage/review",
        data={
            "key": _key("single_bid", "12345678000199"),
            "status": "rejected",
            "reviewer": reviewer,
            "filter": "pending_review",
        },
        follow_redirects=True,
    )


@then(parsers.parse('a página lista o sinal "{signal_type}" de "{cnpj}"'))
def page_lists_signal(context: dict[str, Any], signal_type: str, cnpj: str) -> None:
    response = context["response"]
    assert response.status_code == 200
    assert signal_type in response.text
    assert cnpj in response.text


@then(
    parsers.parse('o sinal "{signal_type}" de "{cnpj}" fica "confirmed" por "{reviewer}"')
)
def signal_confirmed(
    context: dict[str, Any], signal_type: str, cnpj: str, reviewer: str
) -> None:
    assert context["response"].status_code == 303
    doc = context["db"].col.docs[_key(signal_type, cnpj)]
    assert doc["status"] == "confirmed"
    assert doc["reviewed_by"] == reviewer


@then("a página de triagem mostra um aviso de erro")
def error_banner(context: dict[str, Any]) -> None:
    response = context["response"]
    assert response.status_code == 200
    assert 'class="banner"' in response.text


@then(parsers.parse('o sinal "{signal_type}" de "{cnpj}" segue "pending_review"'))
def signal_still_pending(context: dict[str, Any], signal_type: str, cnpj: str) -> None:
    doc = context["db"].col.docs[_key(signal_type, cnpj)]
    assert doc["status"] == "pending_review"


@then("o usuário é redirecionado para o login")
def redirected_to_login(context: dict[str, Any]) -> None:
    response = context["response"]
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"
