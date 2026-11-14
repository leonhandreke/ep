from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables prefixed with ``EP_``."""

    model_config = SettingsConfigDict(env_prefix="EP_")

    database_url: str = "postgresql+psycopg://ep:ep@localhost:5432/ep"
    # Managed library: EP downloads into here (read-write).
    library_dir: Path = Path("/library")
    # Watched directories (read-only). JSON list in the environment,
    # e.g. EP_WATCH_DIRS='["/watch/Library"]'.
    watch_dirs: list[Path] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()
