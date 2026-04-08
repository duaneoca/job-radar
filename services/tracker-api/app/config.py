from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://jobradar:jobradar_dev@localhost:5432/jobradar"
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "development"
    secret_key: str = "change-me-in-production"

    class Config:
        env_file = ".env"


settings = Settings()
