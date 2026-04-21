from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    tracker_api_url: str = "http://localhost:8000"
    environment: str = "development"

    # Adzuna API credentials (free tier, register at
    # https://developer.adzuna.com/). If unset, the Adzuna scraper
    # logs a warning and returns an empty list.
    adzuna_app_id: Optional[str] = None
    adzuna_app_key: Optional[str] = None

    class Config:
        env_file = ".env"


settings = Settings()
