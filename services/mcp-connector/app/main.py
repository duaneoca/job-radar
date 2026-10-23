"""MCP connector — Job Radar for Claude, ChatGPT, Gemini and Grok.

Layout (public, through the frontend nginx):
  GET  /health                                       liveness (no auth)
  GET  /.well-known/oauth-protected-resource[/mcp]   RFC 9728 metadata (no auth)
  POST /mcp                                          MCP streamable HTTP (bearer)

Sign-in is Job Radar's own OAuth server (tracker-api app/oauth.py). This
service only checks that a request carries a live token before the MCP SDK
runs — a missing or dead one gets the 401 + WWW-Authenticate that makes the
assistant start sign-in (a tool error inside a 200 would not) — and hands the
token to the tools through a ContextVar. Permissions are tracker-api's.
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app import api
from app.config import settings
from app.context import bearer_var
from app.tools import mcp

logger = logging.getLogger(__name__)

SCOPES = ["jobs:read", "jobs:write", "generate"]
TOKEN_PREFIX = "jr_at_"
# A checked token is trusted this long before tracker-api is asked again.
# Every tool call still goes to tracker-api with the token, so a revoked token
# fails there at once; this only saves the extra round trip per MCP message.
VALID_FOR = 60.0
_MAX_CACHE = 1000
_valid: dict[str, float] = {}

# The SDK refuses Host headers it doesn't know (DNS-rebinding protection). Its
# default allows only localhost, which would reject every request through
# job-radar.net.
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[settings.public_host, "localhost:*", "127.0.0.1:*", "jobradar-mcp-connector:*"],
    allowed_origins=[settings.public_base_url.rstrip("/")],
)
_mcp_app = mcp.streamable_http_app()      # serves POST /mcp


def _resource_metadata() -> dict:
    return {
        "resource": settings.mcp_url,               # must equal the URL users paste, exactly
        "authorization_servers": [settings.issuer],  # Job Radar's own OAuth server
        "scopes_supported": SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_name": "Job Radar",
    }


async def _prm(request: Request) -> JSONResponse:
    return JSONResponse(_resource_metadata(), headers={"Cache-Control": "public, max-age=300"})


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-connector"})


def _challenge(error: str | None = None) -> JSONResponse:
    meta = f"{settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource/mcp"
    parts = ["Bearer"]
    if error:
        parts.append(f'error="{error}",')
    parts.append(f'resource_metadata="{meta}", scope="{" ".join(SCOPES)}"')
    return JSONResponse({"error": error or "unauthorized",
                         "error_description": "Sign in to Job Radar to use this connector."},
                        status_code=401, headers={"WWW-Authenticate": " ".join(parts)})


async def _token_is_live(token: str) -> bool:
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    if _valid.get(key, 0) > now:
        return True
    try:
        r = await api.client().get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    except Exception:
        logger.warning("tracker-api unreachable while checking a token")
        return False
    if r.status_code != 200:
        _valid.pop(key, None)
        return False
    if len(_valid) >= _MAX_CACHE:
        for k in [k for k, exp in _valid.items() if exp <= now] or list(_valid)[: _MAX_CACHE // 2]:
            _valid.pop(k, None)
    _valid[key] = now + VALID_FOR
    return True


class _BearerGate:
    """Pure ASGI: 401 before the MCP SDK sees anything without a live token."""

    def __init__(self, inner):
        self._inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self._inner(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode(errors="replace")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX):
            return await _challenge()(scope, receive, send)
        if not await _token_is_live(token):
            return await _challenge("invalid_token")(scope, receive, send)
        reset = bearer_var.set(token)
        try:
            await self._inner(scope, receive, send)
        finally:
            bearer_var.reset(reset)


@asynccontextmanager
async def _lifespan(_app):
    # Starlette doesn't run a mounted app's lifespan; the session manager's task
    # group must be started here (the mcp-writer lesson).
    async with mcp.session_manager.run():
        yield
    await api.aclose()


_starlette = Starlette(
    routes=[
        Route("/health", _health),
        Route("/.well-known/oauth-protected-resource/mcp", _prm),
        Route("/.well-known/oauth-protected-resource", _prm),
        Mount("/", app=_mcp_app),
    ],
    lifespan=_lifespan,
)

app = _BearerGate(_starlette)
