"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from unicodedata import category
from urllib.parse import urlsplit

import httpx
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_ROOT / ".env"
DEFAULT_AUTH_HMAC_SECRET = "development-only-visual-scan-auth-secret-change-me"


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
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5500"])
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
    ai_timeout_seconds: float = Field(default=90, gt=0)
    ai_max_input_chars: int = Field(default=50_000, gt=0)
    ai_max_output_tokens: int = Field(default=1_200, gt=0)
    ai_response_format: Literal["json_object", "prompt_only"] = "json_object"
    scans_database_path: Path = BACKEND_ROOT / "data" / "visual-scan.db"
    scans_database_busy_timeout_ms: int = Field(default=5_000, ge=1, le=60_000)
    scans_max_text_chars: int = Field(default=250_000, gt=0)
    auth_cookie_name: str = "visual_scan_session"
    auth_cookie_secure: bool = False
    auth_absolute_lifetime_seconds: int = Field(default=7 * 24 * 60 * 60, gt=0)
    auth_idle_lifetime_seconds: int = Field(default=24 * 60 * 60, gt=0)
    auth_touch_interval_seconds: int = Field(default=5 * 60, gt=0)
    auth_hmac_secret: SecretStr = SecretStr(DEFAULT_AUTH_HMAC_SECRET)

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Keep routing and cookie paths stable and unambiguous."""
        if not value.startswith("/") or (value != "/" and value.endswith("/")):
            raise ValueError("API_PREFIX must start with / and must not end with /.")
        if "?" in value or "#" in value or "\\" in value or ".." in value.split("/"):
            raise ValueError("API_PREFIX must be a normalized URL path.")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        """Require canonical explicit origins suitable for credentialed CORS."""
        if not values:
            raise ValueError("CORS_ORIGINS must contain at least one explicit origin.")
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value or value == "*":
                raise ValueError("CORS origins must be explicit HTTP(S) origins.")
            if any(character.isspace() or category(character) == "Cc" for character in value):
                raise ValueError("CORS origins must not contain whitespace or controls.")
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or value != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError("CORS origins must contain only scheme, host, and optional port.")
            if value not in normalized:
                normalized.append(value)
        return normalized

    @field_validator("scans_database_path", mode="before")
    @classmethod
    def resolve_scans_database_path(cls, value: object) -> Path:
        """Resolve relative scan database paths from the backend directory."""
        if isinstance(value, Path):
            path = value
        elif isinstance(value, str):
            raw_path = value.strip()
            if not raw_path or raw_path == ":memory:":
                raise ValueError("SCANS_DATABASE_PATH must name a filesystem database file.")
            path = Path(raw_path)
        else:
            raise ValueError("SCANS_DATABASE_PATH must be a filesystem path.")

        if str(path) == ":memory:":
            raise ValueError("SCANS_DATABASE_PATH does not support in-memory databases.")
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path.resolve()

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> Self:
        """Normalize runtime settings and reject unsafe enabled deployments."""
        self.environment = self.environment.strip().casefold()
        forbidden_cookie_chars = set("()<>@,;:/[]?={} \t") | {"\\", '"'}
        if not self.auth_cookie_name or any(
            character in forbidden_cookie_chars for character in self.auth_cookie_name
        ):
            raise ValueError("AUTH_COOKIE_NAME must be a valid cookie token.")
        if self.auth_idle_lifetime_seconds > self.auth_absolute_lifetime_seconds:
            raise ValueError("Auth idle lifetime cannot exceed absolute lifetime.")
        if self.auth_touch_interval_seconds > self.auth_idle_lifetime_seconds:
            raise ValueError("Auth touch interval cannot exceed idle lifetime.")
        auth_secret = self.auth_hmac_secret.get_secret_value().encode("utf-8")
        if self.environment not in {"development", "test"} and (
            len(auth_secret) < 32
            or self.auth_hmac_secret.get_secret_value() == DEFAULT_AUTH_HMAC_SECRET
        ):
            raise ValueError("AUTH_HMAC_SECRET must be an explicit secret of at least 32 bytes.")

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
