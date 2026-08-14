"""BDD step definitions for the Capiba dashboard portal.

Feature file: tests/bdd/features/portal.feature
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from capiba import config
from capiba.api import portal
from capiba.api.main import app
from capiba.pipeline import lake

scenarios("features/portal.feature")

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


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (HTTP responses)."""
    return {}


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


@given("as fontes de estatísticas estão indisponíveis")
def stats_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> Any:
        raise ConnectionError("down")

    monkeypatch.setattr(lake, "read_silver_contracts", _raise)
    monkeypatch.setattr(lake, "read_fraud_signals", _raise)


@when("o usuário acessa a página inicial do portal")
def access_portal(context: dict[str, Any]) -> None:
    client = TestClient(app)
    context["response"] = client.get("/", follow_redirects=False)


@then("a página lista os serviços do cluster")
def page_lists_services(context: dict[str, Any]) -> None:
    response = context["response"]
    assert response.status_code == 200
    assert "Capiba" in response.text
    for service in portal.SERVICES:
        assert service["name"] in response.text


@then("o usuário é redirecionado para o login")
def redirected_to_login(context: dict[str, Any]) -> None:
    response = context["response"]
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


@then("a página é exibida com estatísticas indisponíveis")
def page_stats_unavailable(context: dict[str, Any]) -> None:
    response = context["response"]
    assert response.status_code == 200
    assert "indisponível" in response.text
