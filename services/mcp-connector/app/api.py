"""tracker-api calls on the user's behalf, with their own bearer token.

One AsyncClient per process: building a client per call fragments native
memory (the ai-reviewer leak, 2026-10). Errors become ToolError with
tracker-api's own message, which is written for people ("Daily limit
reached…", "Connected apps can only…").
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from app.config import settings
from app.context import bearer_var

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.tracker_api_url, timeout=20,
            # Below uvicorn's 5 s keep-alive, as in ai-reviewer (2026-10-22).
            limits=httpx.Limits(keepalive_expiry=2.0),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _headers() -> dict[str, str]:
    token = bearer_var.get()
    if not token:
        raise ToolError("Not signed in to Job Radar.")
    return {"Authorization": f"Bearer {token}"}


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except Exception:
        d = None
    if isinstance(d, str) and d:
        return d
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d[0].get("msg") or str(d[0])
    return {401: "Your Job Radar sign-in has expired — reconnect the Job Radar connector.",
            403: "Job Radar doesn't allow connected apps to do that.",
            404: "No such job on your Job Radar list.",
            429: "Job Radar's daily limit for this was reached."}.get(r.status_code,
                                                                       f"Job Radar returned an error ({r.status_code}).")


async def call(method: str, path: str, *, params: Any = None, json: Any = None) -> Any:
    try:
        r = await client().request(method, path, params=params, json=json, headers=_headers())
    except httpx.HTTPError:
        raise ToolError("Job Radar isn't responding right now. Try again in a minute.")
    if r.status_code >= 400:
        raise ToolError(_detail(r))
    return r.json() if r.content else None
