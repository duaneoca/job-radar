from contextvars import ContextVar

# Set per-request by ASGI middleware; read inside tool handlers.
agent_key_var: ContextVar[str] = ContextVar("agent_key", default="")
