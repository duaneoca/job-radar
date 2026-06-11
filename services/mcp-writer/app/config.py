from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tracker_api_url: str = "http://jobradar-tracker-api"
    port: int = 8001

    class Config:
        env_file = ".env"


settings = Settings()
