from datetime import timedelta
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/budget_buddy"
    cors_allow_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = True
    cookie_name: str = "__Host-session"
    session_ttl_days: timedelta = timedelta(days=14)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
