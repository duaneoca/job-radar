from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    tracker_api_url: str = "http://localhost:8000"
    # Accept either ANTHROPIC_API_KEY (the SDK's conventional name)
    # or CLAUDE_API_KEY for backwards compatibility.
    anthropic_api_key: str = ""
    environment: str = "development"

    # Shared secret for authenticating internal calls to tracker-api (sent as
    # X-Internal-Token). Must match AGENT_INTERNAL_TOKEN in tracker-api-secrets.
    # Empty = header omitted (transitional; tracker-api enforces in a later phase).
    agent_internal_token: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
