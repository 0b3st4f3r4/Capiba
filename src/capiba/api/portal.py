"""Capiba dashboard portal.

Responsibility: landing page with links to the cluster UIs and
cross-service lake statistics, behind Keycloak SSO when enabled.

Dependencies: authlib, capiba.config, capiba.api.services,
capiba.pipeline.lake
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import quote

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from capiba import config
from capiba.api import services
from capiba.db import triage as triage_db
from capiba.db.arangodb import execute_aql
from capiba.db.triage import TriageError, TriageStatus
from capiba.pipeline import lake

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portal"])

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

oauth = OAuth()


def _service_url(sub: str, suffix: str = "") -> str:
    """Builds the ingress URL of a cluster service."""
    return f"https://{sub}.{config.PORTAL_DOMAIN}:8443{suffix}"


SERVICES: list[dict[str, str]] = [
    {
        "name": "API Docs",
        "description": "Documentação interativa da API (Swagger UI)",
        "url": "/docs",
    },
    {
        "name": "Grafana",
        "description": "Dashboards (BI)",
        "url": _service_url("grafana"),
    },
    {
        "name": "Airflow",
        "description": "Orquestração dos pipelines (DAGs)",
        "url": _service_url("airflow"),
    },
    {
        "name": "Lakekeeper",
        "description": "Lakekeeper — catálogo Iceberg",
        "url": _service_url("iceberg", "/ui/"),
    },
    {
        "name": "MinIO",
        # Entra pelo endpoint S3 (não pelo host do console): o browser aceita
        # a exceção do certificado desse host — exigida pelo preview de
        # tabelas da UI do Lakekeeper — e o MinIO redireciona ao console.
        "description": (
            "MinIO Console (abre via endpoint S3; aceite a exceção do "
            "certificado para liberar o preview de tabelas no Lakekeeper)"
        ),
        "url": _service_url("s3"),
    },
    {
        "name": "Marquez",
        "description": "Catálogo de dados e lineage (OpenLineage)",
        "url": _service_url("marquez"),
    },
    {
        "name": "Trino",
        "description": "SQL sobre o data lake",
        "url": _service_url("trino"),
    },
    {
        "name": "Keycloak",
        "description": "Keycloak — identidade (SSO)",
        "url": _service_url("keycloak"),
    },
    {
        "name": "Headlamp",
        "description": "Kubernetes dashboard (port-forward)",
        "url": "http://localhost:4466",
    },
]


def _keycloak_metadata() -> dict[str, Any]:
    """Fetches OIDC metadata via the internal HTTP issuer.

    Backchannel metadata fetch uses plain HTTP/8088; the pinned issuer is
    HTTPS and the advertised endpoints (token, jwks) are reached over
    HTTPS/8443 with the mounted capiba-tls CA (SSL_CERT_FILE). The
    browser-facing authorization endpoint is rewritten to the public HTTPS
    ingress.
    """
    metadata_url = f"{config.KEYCLOAK_ISSUER}/.well-known/openid-configuration"
    return cast(dict[str, Any], httpx.get(metadata_url, timeout=10.0).json())


def _public_authorization_endpoint(metadata: dict[str, Any]) -> str:
    """Returns the authorization endpoint rewritten for the public HTTPS ingress."""
    auth_endpoint = cast(str, metadata["authorization_endpoint"])
    internal_prefix = config.KEYCLOAK_ISSUER.rstrip("/")
    public_prefix = config.KEYCLOAK_PUBLIC_ISSUER.rstrip("/")
    if auth_endpoint.startswith(internal_prefix):
        return public_prefix + auth_endpoint[len(internal_prefix):]
    return auth_endpoint


def register_keycloak(oauth_client: OAuth) -> None:
    """Registers the Keycloak OIDC client with public HTTPS authorization endpoint."""
    metadata = _keycloak_metadata()
    oauth_client.register(
        name="keycloak",
        client_id=config.KEYCLOAK_CLIENT_ID,
        client_secret=config.KEYCLOAK_CLIENT_SECRET,
        server_metadata_url=f"{config.KEYCLOAK_ISSUER}/.well-known/openid-configuration",
        authorize_url=_public_authorization_endpoint(metadata),
        client_kwargs={"scope": "openid profile email"},
    )


if config.SSO_ENABLED and config.KEYCLOAK_ISSUER:
    register_keycloak(oauth)


def _count_fraud_signals() -> int:
    """Counts signal documents in ArangoDB (same read path as the signals router)."""
    db = services.get_db()
    rows = execute_aql(db, "RETURN LENGTH(signals)")
    return int(cast(Any, rows[0]))


def collect_stats() -> dict[str, int | None]:
    """Cross-service lake statistics; failures degrade to None, never break.

    Uses Trino counts (``lake.count_*``): scanning the full silver into
    memory just to count rows OOMKilled the API pod on real volume.
    """
    try:
        contracts: int | None = lake.count_silver_contracts()
    except Exception as exc:
        logger.warning("Silver contracts stat unavailable: %s", exc)
        contracts = None
    try:
        fraud_signals: int | None = lake.count_fraud_signals()
    except Exception as exc:
        logger.warning("Fraud signals stat unavailable: %s", exc)
        fraud_signals = None
    return {"contracts": contracts, "fraud_signals": fraud_signals}


@router.get("/")
async def portal(request: Request) -> Response:
    """Renders the portal landing page (redirects to SSO login when required)."""
    user = request.session.get("user")
    if config.SSO_ENABLED and user is None:
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "portal.html",
        {"user": user, "services": SERVICES, "stats": collect_stats()},
    )


@router.get("/auth/login")
async def login(request: Request) -> Response:
    """Starts the Keycloak OIDC flow (no-op redirect when SSO is disabled)."""
    if not config.SSO_ENABLED:
        return RedirectResponse("/")
    redirect = await oauth.keycloak.authorize_redirect(
        request, request.url_for("auth_callback")
    )
    return cast(Response, redirect)


@router.get("/auth/callback")
async def auth_callback(request: Request) -> Response:
    """Completes the OIDC flow and stores the user info in the session."""
    # The authorization endpoint is public HTTPS, so the id_token issuer is the
    # public issuer; the metadata fetched by the API is internal HTTP. Accept
    # the public issuer explicitly when validating the token.
    claims_options = {
        "iss": {"values": [config.KEYCLOAK_PUBLIC_ISSUER]},
    }
    token: dict[str, Any] = await oauth.keycloak.authorize_access_token(
        request, claims_options=claims_options
    )
    request.session["user"] = dict(token["userinfo"])
    return RedirectResponse("/")


@router.get("/auth/logout")
async def logout(request: Request) -> Response:
    """Clears the session and returns to the portal."""
    request.session.clear()
    return RedirectResponse("/")


def _reviewer_of(user: dict[str, Any] | None, form_reviewer: str) -> str:
    """Reviewer identity: the form input wins, then the SSO username."""
    return form_reviewer.strip() or (user or {}).get("preferred_username", "")


@router.get("/triage")
async def triage_page(
    request: Request, status: TriageStatus = TriageStatus.PENDING_REVIEW
) -> Response:
    """Editorial triage queue: signals under review + precision report.

    Degrades gracefully: with ArangoDB down the page renders with an
    "indisponível" notice instead of failing.
    """
    user = request.session.get("user")
    if config.SSO_ENABLED and user is None:
        return RedirectResponse("/auth/login", status_code=302)
    entries: list[dict[str, Any]] | None = None
    metrics: list[dict[str, Any]] | None = None
    try:
        db = services.get_db()
        entries = triage_db.list_reviews(db, status=status)
        metrics = triage_db.precision_report(db)
    except Exception as exc:
        logger.warning("Triage page data unavailable: %s", exc)
    return templates.TemplateResponse(
        request,
        "triage.html",
        {
            "user": user,
            "reviewer": _reviewer_of(user, ""),
            "status": str(status),
            "statuses": [str(s) for s in TriageStatus],
            "entries": entries,
            "metrics": metrics,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/triage/review")
async def triage_review(
    request: Request,
    key: Annotated[str, Form()],
    status: Annotated[TriageStatus, Form()],
    reviewer: Annotated[str, Form()] = "",
    reason: Annotated[str | None, Form()] = None,
    filter: Annotated[str, Form()] = str(TriageStatus.PENDING_REVIEW),
) -> Response:
    """Applies an editorial transition from the triage page form.

    The reviewer is the form input (synced from the page's reviewer bar)
    or, when empty, the SSO session username. Validation failures
    (missing reason, invalid transition, unknown key) redirect back to
    the queue with an error banner — form posts never answer 4xx pages.
    """
    user = request.session.get("user")
    if config.SSO_ENABLED and user is None:
        return RedirectResponse("/auth/login", status_code=302)
    try:
        db = services.get_db()
        triage_db.apply_review(
            db, key, status, _reviewer_of(user, reviewer), reason=reason or None
        )
    except HTTPException as exc:
        return RedirectResponse(f"/triage?error={quote(str(exc.detail))}", 303)
    except KeyError:
        return RedirectResponse(f"/triage?error={quote('sinal não encontrado')}", 303)
    except TriageError as exc:
        return RedirectResponse(f"/triage?error={quote(str(exc))}", 303)
    return RedirectResponse(f"/triage?status={quote(filter)}", 303)
