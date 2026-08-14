"""Tests for the Capiba dashboard portal.

Responsibility: Validate the landing page, the SSO redirect flow
(Keycloak mocked) and the stats degradation path (no real
Keycloak, lake or database).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from capiba import config
from capiba.api import portal, services
from capiba.api.main import app
from capiba.pipeline import lake

USERINFO = {"preferred_username": "capiba", "email": "capiba@capiba.local"}
_FAKE_KEYCLOAK_ISSUER = "http://keycloak.capiba.local:8088/realms/capiba"
_FAKE_KEYCLOAK_METADATA: dict[str, Any] = {
    "issuer": _FAKE_KEYCLOAK_ISSUER,
    "authorization_endpoint": (f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/auth"),
    "token_endpoint": f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/token",
    "userinfo_endpoint": (f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/userinfo"),
}


class _FakeMetadataResponse:
    """Minimal httpx response stub for the OIDC metadata endpoint."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client (keeps session cookies across requests)."""
    return TestClient(app)


@pytest.fixture
def keycloak_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: fake Keycloak OIDC metadata so register_keycloak needs no network."""
    monkeypatch.setattr(config, "KEYCLOAK_ISSUER", _FAKE_KEYCLOAK_ISSUER)
    monkeypatch.setattr(
        httpx,
        "get",
        lambda _url, **_kwargs: _FakeMetadataResponse(_FAKE_KEYCLOAK_METADATA),
    )


@pytest.fixture
def sso_enabled(keycloak_metadata: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: SSO enabled with a registered Keycloak client."""
    monkeypatch.setattr(config, "SSO_ENABLED", True)
    portal.register_keycloak(portal.oauth)


@pytest.fixture
def keycloak_token(keycloak_metadata: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: Keycloak access-token exchange returning a fixed userinfo."""

    async def fake_authorize_access_token(request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"userinfo": USERINFO}

    portal.register_keycloak(portal.oauth)
    monkeypatch.setattr(
        portal.oauth.keycloak,
        "authorize_access_token",
        fake_authorize_access_token,
    )


@pytest.fixture
def stats_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: lake stats available with deterministic counts."""
    monkeypatch.setattr(lake, "read_silver_contracts", lambda: [{}, {}, {}])
    monkeypatch.setattr(lake, "read_fraud_signals", lambda: [{}, {}, {}, {}, {}, {}, {}])


@pytest.fixture
def stats_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: every stats source raises (lake down)."""

    def _raise() -> Any:
        raise ConnectionError("lake is down")

    monkeypatch.setattr(lake, "read_silver_contracts", _raise)
    monkeypatch.setattr(lake, "read_fraud_signals", _raise)


@pytest.mark.usefixtures("stats_ok")
class TestPortalWithoutSso:
    """Tests for the portal with SSO disabled (default)."""

    def test_portal_ok(self, client: TestClient) -> None:
        """The landing page must render with the service links and stats."""
        response = client.get("/")
        assert response.status_code == 200
        body = response.text
        assert "Capiba" in body
        for service in portal.SERVICES:
            assert service["name"] in body
            assert service["url"] in body
        assert ">3<" in body
        assert ">7<" in body

    def test_login_redirects_home_when_sso_disabled(self, client: TestClient) -> None:
        """/auth/login is a no-op redirect when SSO is disabled."""
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/"

    def test_services_circle(self, client: TestClient) -> None:
        """Services must render as circle segments with tooltip data."""
        response = client.get("/")
        body = response.text
        assert 'class="services-circle"' in body
        assert body.count('class="seg"') == len(portal.SERVICES)
        assert 'id="seg-tip"' in body
        for service in portal.SERVICES:
            assert service["description"] in body


@pytest.mark.usefixtures("sso_enabled", "stats_ok")
class TestPortalWithSso:
    """Tests for the portal with SSO enabled."""

    def test_anonymous_redirected_to_login(self, client: TestClient) -> None:
        """An anonymous user must be redirected to /auth/login."""
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    def test_callback_logs_user_in(
        self, client: TestClient, keycloak_token: None
    ) -> None:
        """After the OIDC callback the page must show the logged username."""
        response = client.get("/auth/callback")
        assert response.status_code == 200
        assert "Logado como" in response.text
        assert USERINFO["preferred_username"] in response.text

    def test_logout_clears_session(
        self, client: TestClient, keycloak_token: None
    ) -> None:
        """Logout must clear the session and redirect back to the login."""
        client.get("/auth/callback")
        response = client.get("/auth/logout", follow_redirects=False)
        assert response.status_code == 307
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"


@pytest.mark.usefixtures("stats_down")
class TestPortalStatsFailure:
    """Stats sources down must degrade to 'indisponível', never break."""

    def test_page_still_renders(self, client: TestClient) -> None:
        """The page must render 200 with unavailable stats."""
        response = client.get("/")
        assert response.status_code == 200
        assert "indisponível" in response.text


class TestStatsHelpers:
    """Unit tests for the stats collection helpers."""

    def test_count_fraud_signals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The fraud-signals count must use the ArangoDB read path."""
        monkeypatch.setattr(services, "get_capiba_db", lambda: object())
        monkeypatch.setattr(portal, "execute_aql", lambda db, query, b=None: [42])
        assert portal._count_fraud_signals() == 42

    def test_collect_stats_partial_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single failing source must degrade only its own stat."""

        def _raise() -> Any:
            raise ConnectionError("down")

        monkeypatch.setattr(lake, "read_silver_contracts", _raise)
        monkeypatch.setattr(lake, "read_fraud_signals", lambda: [{}, {}, {}, {}, {}])
        assert portal.collect_stats() == {"contracts": None, "fraud_signals": 5}

    def test_register_keycloak_idempotent(
        self, keycloak_metadata: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-registering the Keycloak client must not raise."""
        monkeypatch.setattr(
            config, "KEYCLOAK_PUBLIC_ISSUER", config.KEYCLOAK_PUBLIC_ISSUER
        )
        portal.register_keycloak(portal.oauth)
        assert portal.oauth.keycloak is not None
