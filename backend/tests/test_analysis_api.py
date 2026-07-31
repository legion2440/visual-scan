"""Analysis HTTP contract, error mapping, lifecycle, and isolation tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.core.config import Settings
from app.factory import create_app
from app.features.analysis import router as analysis_router
from app.features.analysis.pipeline import AnalysisPipeline
from app.features.analysis.schemas import AnalysisLanguage
from app.features.analysis.service import AnalysisService

pytestmark = pytest.mark.anyio

VALID_RESULT = {
    "classification": "contract",
    "confidence": 0.93,
    "summary": "Employment agreement.",
    "tags": ["legal", "employment"],
    "fields": [{"label": "Effective date", "value": "2026-07-30"}],
}
VALID_REQUEST = {
    "filename": "contract.jpg",
    "text": "Recognized document text.",
    "language": "eng",
}


class StaticProvider:
    """Return one configured result while recording prompt calls."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[list[dict[str, str]]] = []
        self.close_calls = 0

    async def analyze(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append(messages)
        return deepcopy(self.result)

    async def close(self) -> None:
        self.close_calls += 1


class BrokenService:
    """Raise an unexpected error without exposing its contents to the client."""

    close_calls = 0

    async def analyze(self, **kwargs: Any) -> None:
        raise RuntimeError("full OCR text and provider internals")

    async def close(self) -> None:
        self.close_calls += 1


def enabled_settings(test_settings: Settings, **updates: Any) -> Settings:
    values = {
        "ai_enabled": True,
        "ai_base_url": "http://provider.test/v1",
        "ai_model": "document-model",
        "ai_provider_name": "local-llm",
        **updates,
    }
    return test_settings.model_copy(update=values)


def build_static_service(
    settings: Settings,
    result: dict[str, Any] | None = None,
) -> tuple[AnalysisService, StaticProvider]:
    provider = StaticProvider(VALID_RESULT if result is None else result)
    service = AnalysisService(
        AnalysisPipeline(provider),
        max_input_chars=settings.ai_max_input_chars,
        provider_name=settings.ai_provider_name,
    )
    return service, provider


@asynccontextmanager
async def application_client(application: FastAPI) -> AsyncIterator[AsyncClient]:
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": "http://localhost:5500"},
        ) as client:
            prefix = application.state.settings.api_prefix
            registered = await client.post(
                f"{prefix}/auth/register",
                json={
                    "username": f"user-{uuid4().hex[:12]}",
                    "password": "correct horse battery staple",
                },
            )
            assert registered.status_code == 201
            client.headers["X-CSRF-Token"] = registered.json()["csrf_token"]
            yield client


async def test_exact_analysis_request_and_response_contract(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    settings = enabled_settings(test_settings)
    service, provider = build_static_service(settings)
    monkeypatch.setattr(analysis_router, "_build_analysis_service", lambda unused: service)
    application = create_app(settings)

    async with application_client(application) as client:
        response = await client.post(
            "/api/ai/analyze",
            json={
                "filename": "../folder\\contract\u0000.jpg",
                "text": "  Recognized document text.  ",
                "language": "eng",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "filename": "contract.jpg",
        "classification": "contract",
        "confidence": 0.93,
        "summary": "Employment agreement.",
        "tags": ["legal", "employment"],
        "fields": [{"label": "Effective date", "value": "2026-07-30"}],
        "provider": "local-llm",
    }
    assert len(provider.calls) == 1
    assert provider.close_calls == 1


@pytest.mark.parametrize("language", [item.value for item in AnalysisLanguage])
async def test_all_supported_languages_are_accepted(
    language: str,
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    settings = enabled_settings(test_settings)
    service, _ = build_static_service(settings)
    monkeypatch.setattr(analysis_router, "_build_analysis_service", lambda unused: service)
    application = create_app(settings)

    async with application_client(application) as client:
        response = await client.post(
            "/api/ai/analyze",
            json={**VALID_REQUEST, "language": language},
        )

    assert response.status_code == 200


async def test_whitespace_only_text_returns_422(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    settings = enabled_settings(test_settings)
    service, provider = build_static_service(settings)
    monkeypatch.setattr(analysis_router, "_build_analysis_service", lambda unused: service)
    application = create_app(settings)

    async with application_client(application) as client:
        response = await client.post(
            "/api/ai/analyze",
            json={**VALID_REQUEST, "text": " \r\n\t "},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "OCR text must not be empty."}
    assert provider.calls == []


async def test_input_limit_returns_413(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    settings = enabled_settings(test_settings, ai_max_input_chars=5)
    service, provider = build_static_service(settings)
    monkeypatch.setattr(analysis_router, "_build_analysis_service", lambda unused: service)
    application = create_app(settings)

    async with application_client(application) as client:
        response = await client.post(
            "/api/ai/analyze",
            json={**VALID_REQUEST, "text": "123456"},
        )

    assert response.status_code == 413
    assert "123456" not in response.text
    assert provider.calls == []


@pytest.mark.parametrize(
    "invalid_result",
    [
        {**VALID_RESULT, "classification": "billing document"},
        {**VALID_RESULT, "confidence": 1.5},
        {**VALID_RESULT, "fields": [{"label": 123, "value": "invalid"}]},
        {**VALID_RESULT, "tags": ["LEGAL", " legal "] * 5 + ["LEGAL"]},
    ],
)
async def test_schema_invalid_provider_result_returns_502(
    invalid_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    settings = enabled_settings(test_settings)
    service, _ = build_static_service(settings, invalid_result)
    monkeypatch.setattr(analysis_router, "_build_analysis_service", lambda unused: service)
    application = create_app(settings)

    async with application_client(application) as client:
        response = await client.post("/api/ai/analyze", json=VALID_REQUEST)

    assert response.status_code == 502
    assert response.json() == {"detail": "The AI provider returned an invalid response."}


async def test_disabled_ai_returns_503_without_building_service(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds = 0

    def unexpected_build(settings: Settings) -> AnalysisService:
        nonlocal builds
        builds += 1
        raise AssertionError("Disabled AI must not build a service.")

    monkeypatch.setattr(analysis_router, "_build_analysis_service", unexpected_build)

    response = await client.post("/api/ai/analyze", json=VALID_REQUEST)

    assert response.status_code == 503
    assert response.json() == {"detail": "AI analysis is not enabled."}
    assert builds == 0


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID_REQUEST, "language": "unknown"},
        {**VALID_REQUEST, "text": 123},
        {**VALID_REQUEST, "extra": "not allowed"},
    ],
)
async def test_invalid_requests_return_422(
    client: AsyncClient,
    payload: dict[str, Any],
) -> None:
    response = await client.post("/api/ai/analyze", json=payload)

    assert response.status_code == 422


async def test_unexpected_error_returns_safe_generic_500(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = enabled_settings(test_settings)
    service = BrokenService()
    monkeypatch.setattr(analysis_router, "_build_analysis_service", lambda unused: service)
    application = create_app(settings)

    async with application_client(application) as client:
        response = await client.post("/api/ai/analyze", json=VALID_REQUEST)

    assert response.status_code == 500
    assert response.json() == {"detail": "AI analysis failed unexpectedly."}
    assert "full OCR text" not in response.text
    assert "provider internals" not in response.text
    assert "full OCR text" not in caplog.text
    assert "provider internals" not in caplog.text


async def test_openapi_and_existing_ocr_routes_are_present(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/ai/analyze" in paths
    assert "/api/ocr/recognize" in paths
    assert "/api/ocr/pdf/recognize" in paths


async def test_parallel_first_initialization_builds_one_app_local_service(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    settings = enabled_settings(test_settings)
    service, _ = build_static_service(settings)
    builds = 0

    def build(unused: Settings) -> AnalysisService:
        nonlocal builds
        builds += 1
        return service

    monkeypatch.setattr(analysis_router, "_build_analysis_service", build)
    application = create_app(settings)

    async with application.router.lifespan_context(application):
        request = Request({"type": "http", "app": application})
        services = await asyncio.gather(
            *[analysis_router.get_analysis_service(request) for _ in range(20)]
        )

    assert builds == 1
    assert all(item is service for item in services)


async def test_application_instances_keep_services_isolated(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    built_services: list[AnalysisService] = []

    def build(settings: Settings) -> AnalysisService:
        service, _ = build_static_service(settings)
        built_services.append(service)
        return service

    monkeypatch.setattr(analysis_router, "_build_analysis_service", build)
    first_app = create_app(enabled_settings(test_settings, ai_provider_name="first"))
    second_app = create_app(enabled_settings(test_settings, ai_provider_name="second"))

    async with first_app.router.lifespan_context(first_app):
        first = await analysis_router.get_analysis_service(
            Request({"type": "http", "app": first_app})
        )
        async with second_app.router.lifespan_context(second_app):
            second = await analysis_router.get_analysis_service(
                Request({"type": "http", "app": second_app})
            )

    assert first is not second
    assert built_services == [first, second]


async def test_shutdown_without_created_service_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    builds = 0

    def build(settings: Settings) -> AnalysisService:
        nonlocal builds
        builds += 1
        service, _ = build_static_service(settings)
        return service

    monkeypatch.setattr(analysis_router, "_build_analysis_service", build)
    application = create_app(enabled_settings(test_settings))

    async with application.router.lifespan_context(application):
        pass

    assert builds == 0
