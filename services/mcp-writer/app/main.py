"""
MCP Writer — ASGI entrypoint.

Layout:
  GET  /health           liveness probe (no auth)
  *    /mcp/*            FastMCP streamable-HTTP transport

The pure-ASGI middleware extracts X-Agent-Key before each request and stores
it in a ContextVar so tool handlers can forward it to tracker-api without the
key ever appearing in tool arguments or LLM context.
"""

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.context import agent_key_var
from app.tools import mcp

# Built once here (not inline in the Mount) so the FastMCP session manager exists
# before the lifespan below reaches for it.
_mcp_app = mcp.streamable_http_app()


class _AgentKeyMiddleware:
    """Pure ASGI middleware — safe for SSE / streaming responses."""

    def __init__(self, inner):
        self._inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k: v for k, v in scope.get("headers", [])}
            key = headers.get(b"x-agent-key", b"").decode()
            token = agent_key_var.set(key)
            try:
                await self._inner(scope, receive, send)
            finally:
                agent_key_var.reset(token)
        else:
            await self._inner(scope, receive, send)


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-writer"})


@asynccontextmanager
async def _lifespan(_app):
    """Start the FastMCP session manager.

    Starlette does not run a mounted app's lifespan, so without this the
    streamable-HTTP manager's task group is never created and every request to
    /mcp fails with "Task group is not initialized. Make sure to use run()."
    """
    async with mcp.session_manager.run():
        yield


_starlette = Starlette(
    routes=[
        Route("/health", _health),
        Mount("/mcp", app=_mcp_app),
    ],
    lifespan=_lifespan,
)

app = _AgentKeyMiddleware(_starlette)
