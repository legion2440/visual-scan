"""HTTP contract tests for server-side OCR."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.features.ocr import router as ocr_router
from app.features.ocr.errors import (
    ImageTooLargeError,
    InvalidImageError,
    InvalidOcrParametersError,
    OcrEngineUnavailableError,
    OcrProcessingError,
    OcrTimeoutError,
    UnsupportedImageFormatError,
)
from app.features.ocr.schemas import OcrResponse

pytestmark = pytest.mark.anyio

EXPECTED_RESPONSE = {
    "filename": "scan.png",
    "text": "Hello world",
    "confidence": 91.25,
    "words": 2,
    "language": "eng",
    "preprocessing": "none",
    "threshold": None,
    "width": 640,
    "height": 480,
    "format": "PNG",
    "engine": "tesseract",
}


class FakeOcrService:
    """Capture calls while keeping API tests independent of Tesseract."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.thread_ids: list[int] = []
        self.error: Exception | None = None

    def recognize(self, **kwargs: Any) -> OcrResponse:
        self.calls.append(kwargs)
        self.thread_ids.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        return OcrResponse.model_validate(EXPECTED_RESPONSE)


@pytest.fixture
def fake_service(app: FastAPI) -> FakeOcrService:
    service = FakeOcrService()
    app.dependency_overrides[ocr_router.get_ocr_service] = lambda: service
    return service


async def test_recognize_returns_exact_json_contract(
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"image bytes", "image/png")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == EXPECTED_RESPONSE
    assert len(fake_service.calls) == 1
    call = fake_service.calls[0]
    assert call["filename"] == "scan.png"
    assert call["data"] == b"image bytes"
    assert call["content_type"] == "image/png"
    assert call["language"] == "eng"
    assert call["preprocessing"] == "none"
    assert call["threshold"] is None


async def test_sync_service_runs_outside_the_event_loop_thread(
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    event_loop_thread_id = threading.get_ident()

    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"image bytes", "image/png")},
    )

    assert response.status_code == 200
    assert fake_service.thread_ids
    assert fake_service.thread_ids[0] != event_loop_thread_id


async def test_explicit_parameters_are_passed_to_service(
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.webp", b"webp bytes", "image/webp")},
        data={
            "language": "eng+rus",
            "preprocessing": "threshold",
            "threshold": "175",
        },
    )

    assert response.status_code == 200
    call = fake_service.calls[0]
    assert call["language"] == "eng+rus"
    assert call["preprocessing"] == "threshold"
    assert call["threshold"] == 175


@pytest.mark.parametrize("language", ["eng", "rus", "eng+rus", "deu", "fra", "spa"])
async def test_every_supported_language_reaches_service_unchanged(
    client: AsyncClient,
    fake_service: FakeOcrService,
    language: str,
) -> None:
    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"image bytes", "image/png")},
        data={"language": language},
    )

    assert response.status_code == 200
    assert fake_service.calls[0]["language"] == language


@pytest.mark.parametrize(
    ("data", "expected_fragment"),
    [
        ({"language": "ita"}, "language"),
        ({"preprocessing": "sharpen"}, "preprocessing"),
        ({"threshold": "-1"}, "threshold"),
        ({"threshold": "256"}, "threshold"),
    ],
)
async def test_invalid_form_values_return_422_without_calling_service(
    client: AsyncClient,
    fake_service: FakeOcrService,
    data: dict[str, str],
    expected_fragment: str,
) -> None:
    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"image bytes", "image/png")},
        data=data,
    )

    assert response.status_code == 422
    assert expected_fragment in response.text
    assert fake_service.calls == []


async def test_missing_file_returns_422(
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    response = await client.post("/api/ocr/recognize")

    assert response.status_code == 422
    assert fake_service.calls == []


async def test_empty_file_returns_400_without_calling_service(
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "The uploaded image is empty."}
    assert fake_service.calls == []


async def test_router_reads_only_limit_plus_one_and_returns_413(
    app: FastAPI,
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    app.state.settings.max_image_bytes = 4

    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"12345-extra-data", "image/png")},
    )

    assert response.status_code == 413
    assert "4-byte limit" in response.json()["detail"]
    assert fake_service.calls == []


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (InvalidImageError(), 400),
        (ImageTooLargeError(), 413),
        (UnsupportedImageFormatError(), 415),
        (InvalidOcrParametersError(), 422),
        (OcrEngineUnavailableError(), 503),
        (OcrTimeoutError(), 504),
        (OcrProcessingError(), 500),
    ],
)
async def test_feature_errors_are_mapped_to_http_responses(
    client: AsyncClient,
    fake_service: FakeOcrService,
    error: Exception,
    status_code: int,
) -> None:
    fake_service.error = error

    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"image bytes", "image/png")},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}


async def test_unexpected_error_returns_safe_500_without_internal_details(
    client: AsyncClient,
    fake_service: FakeOcrService,
) -> None:
    fake_service.error = ValueError(r"C:\private\models\secret.traineddata")

    response = await client.post(
        "/api/ocr/recognize",
        files={"file": ("scan.png", b"image bytes", "image/png")},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "OCR processing failed unexpectedly."}
    assert "private" not in response.text


async def test_openapi_contains_ocr_endpoint(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/ocr/recognize" in paths


async def test_health_does_not_construct_ocr_service(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    assert not hasattr(app.state, ocr_router._SERVICE_ATTRIBUTE)

    response = await client.get("/api/health")

    assert response.status_code == 200
    assert not hasattr(app.state, ocr_router._SERVICE_ATTRIBUTE)


async def test_service_dependency_is_lazy_app_local_singleton(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    received_settings: list[object] = []

    def build_service(settings: object) -> object:
        received_settings.append(settings)
        return sentinel

    monkeypatch.setattr(ocr_router, "_build_ocr_service", build_service)
    request = SimpleNamespace(app=app)

    assert ocr_router.get_ocr_service(request) is sentinel
    assert ocr_router.get_ocr_service(request) is sentinel
    assert received_settings == [app.state.settings]
