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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
