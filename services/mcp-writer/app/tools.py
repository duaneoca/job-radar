"""
MCP tools that proxy the tracker-api /agent/* endpoints.

X-Agent-Key is forwarded from the per-request ContextVar set by the ASGI
middleware in main.py.  Tools never accept or trust a key from the caller.
"""

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.context import agent_key_var

mcp = FastMCP("job-radar-writer")


def _headers() -> dict[str, str]:
    key = agent_key_var.get()
    if not key:
        raise ValueError("X-Agent-Key header is required")
    return {"X-Agent-Key": key}


async def _get(path: str) -> Any:
    async with httpx.AsyncClient(base_url=settings.tracker_api_url, timeout=30) as client:
        resp = await client.get(path, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict) -> Any:
    async with httpx.AsyncClient(base_url=settings.tracker_api_url, timeout=30) as client:
        resp = await client.post(path, json=body, headers=_headers())
        resp.raise_for_status()
        return resp.json()


@mcp.tool(description=(
    "Get agent configuration: email provider, folder names, LLM provider/model/key, "
    "and decrypted email credentials. Call once per run to bootstrap the agent."
))
async def get_config() -> dict:
    return await _get("/agent/config")


@mcp.tool(description=(
    "Get the current user's job reviews (company, title, status, URL). "
    "Used for matching emails to existing applications and deduplication."
))
async def get_reviews() -> list:
    return await _get("/agent/reviews")


@mcp.tool(description=(
    "Create an inbox entry for one email. Includes the category classification, "
    "confidence score, and a list of extracted job postings. "
    "Returns inbox_email_id and posting_ids."
))
async def create_inbox_entry(payload: dict) -> dict:
    """
    payload shape (mirrors POST /agent/inbox):
      message_id, subject, sender, received_at (ISO-8601), category, confidence,
      langfuse_trace_id?, raw_extracted_json?,
      postings: [{company, role, link?, action_required, possible_duplicate, matched_review_id?}]
    """
    return await _post("/agent/inbox", payload)


@mcp.tool(description=(
    "Record a status interaction derived from an email "
    "(e.g. application confirmed, interview scheduled, offer received, rejection). "
    "Optionally advances the matched review's status. Returns interaction_id."
))
async def record_interaction(payload: dict) -> dict:
    """
    payload shape (mirrors POST /agent/interactions):
      message_id, subject, sender, received_at (ISO-8601), category, confidence,
      matched_review_id?, match_confidence, new_status?, timeline_note?,
      langfuse_trace_id?
    """
    return await _post("/agent/interactions", payload)


@mcp.tool(description=(
    "Register a pending Human-in-the-Loop (HITL) decision before posting "
    "a disambiguation prompt to Slack. The caller supplies a stable hitl_id "
    "and the list of candidate review UUIDs for the user to choose from."
))
async def register_hitl(hitl_id: str, candidates: list[str]) -> dict:
    """
    candidates: list of review_id UUIDs (strings) the user will choose between.
    Returns the registered HITL record.
    """
    return await _post("/agent/hitl/register", {"hitl_id": hitl_id, "candidates": candidates})


@mcp.tool(description=(
    "Report a completed agent run (operational heartbeat). "
    "Call at the end of every run regardless of outcome. Returns run_id."
))
async def report_run(record: dict) -> dict:
    """
    record shape (mirrors POST /agent/runs):
      environment (local|cloud), agent_version, status (success|partial|failed),
      started_at (ISO-8601), finished_at?, emails_processed, postings_created,
      interactions_recorded, escalations, retries, error_summary?
    """
    return await _post("/agent/runs", record)
