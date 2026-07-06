from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    tracker_api_url: str = "http://localhost:8000"
    environment: str = "development"

    # Shared secret for authenticating internal calls to tracker-api (sent as
    # X-Internal-Token). Must match AGENT_INTERNAL_TOKEN in tracker-api-secrets.
    # Empty = header omitted (transitional; tracker-api enforces in a later phase).
    agent_internal_token: str = ""

    # Adzuna is BYOK — each user supplies their own app_id/app_key via Settings,
    # fetched per-scrape from tracker-api. No shared/global Adzuna key.

    class Config:
        env_file = ".env"


settings = Settings()
