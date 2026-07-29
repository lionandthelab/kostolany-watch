"""Runtime settings for connectors and model backends."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")
    cache_dir: str = Field(default="artifacts/cache", validation_alias="CACHE_DIR")
    data_start: str = Field(default="2010-01-01", validation_alias="DATA_START")
    # `tsfm_backend` was removed with ChronosTSFM: `build_tsfm()` was its only
    # consumer and now returns LocalTSFM unconditionally. `extra="ignore"` above
    # means a stale KOSTOLANY_TSFM_BACKEND in someone's .env is still harmless.
    http_timeout: float = 30.0

    @property
    def cache_path(self) -> Path:
        p = Path(self.cache_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
