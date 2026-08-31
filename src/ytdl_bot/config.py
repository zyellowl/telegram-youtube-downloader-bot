from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    download_dir: Path = Field(default=Path("downloads"), alias="DOWNLOAD_DIR")
    max_concurrent_downloads: int = Field(default=2, alias="MAX_CONCURRENT_DOWNLOADS")
    max_tasks_per_user: int = Field(default=1, alias="MAX_TASKS_PER_USER")
    max_duration_seconds: int = Field(default=0, alias="MAX_DURATION_SECONDS")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    max_source_bytes: int = Field(default=2_000_000_000, alias="MAX_SOURCE_BYTES")
    telegram_api_base_url: str | None = Field(default=None, alias="TELEGRAM_API_BASE_URL")
    telegram_proxy_url: str | None = Field(default=None, alias="TELEGRAM_PROXY_URL")
    cleanup_max_age_seconds: int = Field(default=24 * 60 * 60, alias="CLEANUP_MAX_AGE_SECONDS")
    request_cache_ttl_seconds: int = Field(default=30 * 60, alias="REQUEST_CACHE_TTL_SECONDS")
    max_cached_requests: int = Field(default=512, alias="MAX_CACHED_REQUESTS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    def __init__(self, **data):
        normalized = {key.upper(): value for key, value in data.items()}
        super().__init__(**normalized)

    @cached_property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for value in self.admin_user_ids.split(","):
            value = value.strip()
            if value:
                ids.add(int(value))
        return ids

    @property
    def uses_local_bot_api(self) -> bool:
        return bool(self.telegram_api_base_url)
