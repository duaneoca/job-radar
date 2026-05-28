from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://jobradar:jobradar_dev@localhost:5432/jobradar"
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "development"

    # JWT — sign tokens with this secret (keep it long and random in production)
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    # Bootstrap admin — on startup, if no users exist and these are set,
    # the admin account is created automatically.
    admin_email: str = ""
    admin_password: str = "changeme123"   # forced change on first login

    # Job pool — unactioned jobs older than this many days are soft-expired.
    job_ttl_days: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
