"""Health endpoint and application factory tests."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import factory
from app.core.config import BACKEND_ROOT, ENV_FILE, Settings, get_settings
from app.factory import create_app

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


async def test_openapi_contains_health_and_ai_analysis(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/health" in paths
    assert "/api/ai/analyze" in paths


async def test_allowed_cors_origin_receives_header(
    client: AsyncClient,
) -> None:
    origin = "http://localhost:5500"
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


async def test_default_cors_does_not_advertise_cross_site_loopback_origin() -> None:
    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:5500"]
    assert "http://127.0.0.1:5500" not in settings.cors_origins


async def test_default_ai_timeout_leaves_frontend_response_margin() -> None:
    settings = Settings(_env_file=None)

    assert settings.ai_timeout_seconds == 90


async def test_credentialed_cors_preflight_uses_explicit_methods_and_headers(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.options(
        "/api/scans",
        headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5500"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "x-csrf-token" in response.headers["access-control-allow-headers"].lower()


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


async def test_factory_module_does_not_create_production_app() -> None:
    assert not hasattr(factory, "app")


async def test_env_file_is_bound_to_backend_directory() -> None:
    assert Path(__file__).resolve().parents[1] == BACKEND_ROOT
    assert Path(ENV_FILE) == BACKEND_ROOT / ".env"
    assert Settings.model_config["env_prefix"] == "VISUAL_SCAN_"


async def test_get_settings_returns_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    calls = 0

    def build_settings() -> object:
        nonlocal calls
        calls += 1
        return sentinel

    monkeypatch.setattr("app.core.config.Settings", build_settings)
    get_settings.cache_clear()
    try:
        assert get_settings() is sentinel
        assert get_settings() is sentinel
        assert calls == 1
    finally:
        get_settings.cache_clear()


async def test_health_reports_enabled_configuration_without_creating_service(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(
        update={
            "ai_enabled": True,
            "ai_base_url": "http://provider.test/v1",
            "ai_model": "test-model",
            "ai_provider_name": "local-llm",
        }
    )
    application = create_app(settings)

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            response = await test_client.get("/api/health")

        assert not hasattr(application.state, "_visual_scan_analysis_service")

    assert response.json() == {
        "status": "ok",
        "ai_available": True,
        "provider": "local-llm",
    }


async def test_enabled_ai_requires_base_url_and_model() -> None:
    with pytest.raises(ValueError, match="AI_BASE_URL, AI_MODEL"):
        Settings(
            _env_file=None,
            ai_enabled=True,
            ai_base_url="",
            ai_model="",
        )


async def test_disabled_ai_ignores_empty_provider_configuration() -> None:
    settings = Settings(
        _env_file=None,
        ai_enabled=False,
        ai_base_url="not a URL",
        ai_model="",
        ai_provider_name="",
    )

    assert settings.ai_enabled is False
    assert settings.ai_provider_name == ""


@pytest.mark.parametrize(
    "origins",
    [
        ["*"],
        ["http://localhost:5500/"],
        ["http://localhost:5500/path"],
        ["http://user@localhost:5500"],
        [],
    ],
)
async def test_credentialed_cors_origins_must_be_explicit_and_canonical(
    origins: list[str],
) -> None:
    with pytest.raises(ValueError, match="CORS"):
        Settings(_env_file=None, cors_origins=origins)


async def test_production_requires_an_explicit_auth_hmac_secret() -> None:
    with pytest.raises(ValueError, match="AUTH_HMAC_SECRET"):
        Settings(_env_file=None, environment="production")


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///tmp/provider",
        "http://user:secret@provider.test/v1",
        "http://provider.test/v1?query=yes",
        "http://provider.test/v1?",
        "http://provider.test/v1#",
        "http://provider.test:invalid/v1",
        "http://provider.test/v1\nhidden",
        "http://provider.test/v1\rhidden",
        "http://provider.test/v1\thidden",
    ],
)
async def test_enabled_ai_rejects_unsafe_or_invalid_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="AI_BASE_URL"):
        Settings(
            _env_file=None,
            ai_enabled=True,
            ai_base_url=base_url,
            ai_model="document-model",
        )


async def test_enabled_ai_canonicalizes_base_url() -> None:
    settings = Settings(
        _env_file=None,
        ai_enabled=True,
        ai_base_url="HTTP://Provider.Test/v1/",
        ai_model="document-model",
    )

    assert settings.ai_base_url == "http://provider.test/v1"
