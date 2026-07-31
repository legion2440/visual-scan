"""Shared backend test fixtures."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.factory import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Return settings isolated from backend/.env."""
    return Settings(
        _env_file=None,
        app_name="Visual Scan Test API",
        app_version="9.9.9",
        environment="test",
        api_prefix="/api",
        cors_origins=[
            "http://localhost:5500",
            "http://127.0.0.1:5500",
        ],
        host="127.0.0.1",
        port=8000,
        tesseract_cmd="",
        ocr_timeout_seconds=45,
        max_image_bytes=20_971_520,
        max_image_pixels=25_000_000,
        max_pdf_bytes=52_428_800,
        max_pdf_pages=20,
        max_pdf_page_pixels=25_000_000,
        max_pdf_total_pixels=200_000_000,
        pdf_render_dpi=300,
        pdf_timeout_seconds=180,
        ai_enabled=False,
        ai_base_url="",
        ai_api_key="",
        ai_model="",
        ai_provider_name="openai-compatible",
        ai_timeout_seconds=45,
        ai_max_input_chars=50_000,
        ai_max_output_tokens=1_200,
        ai_response_format="json_object",
    )


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    """Create the application through its public factory."""
    return create_app(test_settings)


@pytest.fixture
def anyio_backend() -> str:
    """Run async HTTP tests on Python's built-in asyncio backend."""
    return "asyncio"


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Yield an HTTPX client connected directly to the ASGI application."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield test_client
