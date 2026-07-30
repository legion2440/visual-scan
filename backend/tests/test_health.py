"""Health endpoint and application factory tests."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import BACKEND_ROOT, ENV_FILE, Settings, get_settings
from app.main import create_app

EXPECTED_HEALTH = {
    "status": "ok",
    "ai_available": False,
    "provider": None,
}

pytestmark = pytest.mark.anyio


async def test_health_returns_exact_json_contract(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == EXPECTED_HEALTH
    assert response.json()["ai_available"] is False


async def test_openapi_contains_health_but_not_ai_analysis(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/health" in paths
    assert "/api/ai/analyze" not in paths
    response = await client.post("/api/ai/analyze", json={"text": "not implemented"})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5500", "http://127.0.0.1:5500"],
)
async def test_allowed_cors_origin_receives_header(
    client: AsyncClient,
    origin: str,
) -> None:
    response = await client.get(
        "/api/health",
        headers={"Origin": origin},
    )

    assert response.headers["access-control-allow-origin"] == origin


async def test_unlisted_cors_origin_is_not_allowed(client: AsyncClient) -> None:
    response = await client.get(
        "/api/health",
        headers={"Origin": "https://untrusted.example"},
    )

    assert "access-control-allow-origin" not in response.headers


async def test_application_factory_accepts_explicit_settings() -> None:
    settings = Settings(
        _env_file=None,
        app_name="Factory Test API",
        app_version="3.0.0",
        environment="test",
        api_prefix="/custom-api",
        cors_origins=["https://frontend.example"],
        host="0.0.0.0",
        port=9000,
    )

    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/custom-api/health")

    assert application.title == "Factory Test API"
    assert application.version == "3.0.0"
    assert application.state.settings is settings
    assert response.json() == EXPECTED_HEALTH


async def test_env_file_is_bound_to_backend_directory() -> None:
    assert Path(__file__).resolve().parents[1] == BACKEND_ROOT
    assert Path(ENV_FILE) == BACKEND_ROOT / ".env"
    assert Settings.model_config["env_prefix"] == "VISUAL_SCAN_"


async def test_get_settings_returns_cached_instance() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
