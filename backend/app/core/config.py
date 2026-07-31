"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from unicodedata import category

import httpx
from pydantic import Field, SecretStr, model_validator
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
    max_pdf_bytes: int = Field(default=52_428_800, gt=0)
    max_pdf_pages: int = Field(default=20, gt=0)
    max_pdf_page_pixels: int = Field(default=25_000_000, gt=0)
    max_pdf_total_pixels: int = Field(default=200_000_000, gt=0)
    pdf_render_dpi: int = Field(default=300, ge=72, le=600)
    pdf_timeout_seconds: int = Field(default=180, gt=0)
    ai_enabled: bool = False
    ai_base_url: str = ""
    ai_api_key: SecretStr = SecretStr("")
    ai_model: str = ""
    ai_provider_name: str = "openai-compatible"
    ai_timeout_seconds: float = Field(default=45, gt=0)
    ai_max_input_chars: int = Field(default=50_000, gt=0)
    ai_max_output_tokens: int = Field(default=1_200, gt=0)
    ai_response_format: Literal["json_object", "prompt_only"] = "json_object"

    @model_validator(mode="after")
    def validate_ai_configuration(self) -> Self:
        """Normalize AI settings and reject incomplete enabled deployments."""
        raw_base_url = self.ai_base_url
        self.ai_model = self.ai_model.strip()
        self.ai_provider_name = self.ai_provider_name.strip()

        if not self.ai_enabled:
            self.ai_base_url = raw_base_url.strip().rstrip("/")
            return self
        if not self.ai_provider_name:
            raise ValueError("AI provider name must not be empty when AI is enabled.")

        missing = [
            name
            for name, value in (
                ("AI_BASE_URL", raw_base_url),
                ("AI_MODEL", self.ai_model),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "AI is enabled but required settings are missing: " + ", ".join(missing)
            )

        if (
            "?" in raw_base_url
            or "#" in raw_base_url
            or any(character.isspace() or category(character) == "Cc" for character in raw_base_url)
        ):
            raise ValueError(
                "AI_BASE_URL must not contain whitespace, control characters, "
                "a query, or a fragment."
            )

        try:
            parsed_url = httpx.URL(raw_base_url.rstrip("/"))
        except httpx.InvalidURL as error:
            raise ValueError("AI_BASE_URL is not a valid HTTP(S) URL.") from error
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.host
            or parsed_url.username
            or parsed_url.password
        ):
            raise ValueError(
                "AI_BASE_URL must be an HTTP(S) base URL without credentials, query, or fragment."
            )
        self.ai_base_url = str(parsed_url).rstrip("/")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings instance."""
    return Settings()
