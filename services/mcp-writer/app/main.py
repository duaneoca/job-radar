"""
MCP Writer — ASGI entrypoint.

Layout:
  GET  /health           liveness probe (no auth)
  *    /mcp/*            FastMCP streamable-HTTP transport

The pure-ASGI middleware extracts X-Agent-Key before each request and stores
it in a ContextVar so tool handlers can forward it to tracker-api without the
key ever appearing in tool arguments or LLM context.
"""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.context import agent_key_var
from app.tools import mcp


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


_starlette = Starlette(
    routes=[
        Route("/health", _health),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ],
)

app = _AgentKeyMiddleware(_starlette)
