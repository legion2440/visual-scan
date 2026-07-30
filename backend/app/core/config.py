"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime settings loaded from backend/.env and VISUAL_SCAN_* variables."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="VISUAL_SCAN_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Visual Scan API"
    app_version: str = "0.1.0"
    environment: str = "development"
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5500",
            "http://127.0.0.1:5500",
        ]
    )
    host: str = "127.0.0.1"
    port: int = 8000
    tesseract_cmd: str = ""
    ocr_timeout_seconds: int = Field(default=45, gt=0)
    max_image_bytes: int = Field(default=20_971_520, gt=0)
    max_image_pixels: int = Field(default=25_000_000, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings instance."""
    return Settings()
