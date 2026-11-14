from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_media_roots(data: dict[str, Any]) -> list[Path]:
    return [data["library_dir"], Path("/watch")]


class Settings(BaseSettings):
    """Configuration from environment variables prefixed with ``EP_``."""

    model_config = SettingsConfigDict(env_prefix="EP_")

    database_url: str = "postgresql+psycopg://ep:ep@localhost:5432/ep"
    # Managed library: EP downloads into here (read-write).
    library_dir: Path = Path("/library")
    # Directories file sources may point into (``ep.media_roots``); JSON list in the
    # environment. Default: the managed library and the parent of the watched mounts.
    media_roots: list[Path] = Field(default_factory=_default_media_roots)


@lru_cache
def get_settings() -> Settings:
    return Settings()
