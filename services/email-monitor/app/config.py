from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    tracker_api_url: str = "http://localhost:8000"
    gmail_credentials_file: str = "credentials.json"
    gmail_token_file: str = "token.json"
    poll_interval_seconds: int = 300  # check every 5 minutes
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
