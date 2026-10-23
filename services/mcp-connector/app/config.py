from urllib.parse import urlsplit

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # In-cluster tracker-api. Every tool call goes there with the user's own
    # bearer token; tracker-api decides what the token may do.
    tracker_api_url: str = "http://jobradar-tracker-api"
    # The public origin users connect to (the OAuth issuer too). The connector's
    # URL is f"{public_base_url}/mcp" and must match what users paste exactly.
    public_base_url: str = "http://localhost:8002"
    # Job Radar's OAuth issuer. In production it IS public_base_url (same host,
    # tracker-api behind /api); separate only so a local run can point elsewhere.
    oauth_issuer: str = ""
    log_level: str = "INFO"

    class Config:
        env_file = ".env"

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/mcp"

    @property
    def issuer(self) -> str:
        return (self.oauth_issuer or self.public_base_url).rstrip("/")

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_base_url).netloc


settings = Settings()
