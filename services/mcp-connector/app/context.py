from contextvars import ContextVar

# The caller's OAuth access token, set per request by the auth middleware in
# main.py and read by the tools. Never a tool argument: it must not pass
# through the model's context.
bearer_var: ContextVar[str] = ContextVar("bearer", default="")
