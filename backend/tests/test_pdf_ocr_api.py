"""HTTP contract tests for server-side PDF OCR."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.features.ocr import router as ocr_router
from app.features.ocr.errors import (
    EmptyPdfError,
    InvalidOcrParametersError,
    InvalidPdfError,
    InvalidPdfPasswordError,
    OcrEngineUnavailableError,
    OcrTimeoutError,
    PdfRenderError,
    PdfTooLargeError,
    UnsupportedPdfFormatError,
    UnsupportedPdfSecurityError,
)
from app.features.ocr.schemas import PdfOcrResponse

pytestmark = pytest.mark.anyio

EXPECTED_PDF_RESPONSE = {
    "filename": "document.pdf",
    "text": "First page\n\nSecond page",
    "page_count": 2,
    "language": "eng",
    "preprocessing": "grayscale",
    "threshold": None,
    "render_dpi": 300,
    "pages": [
        {
            "page": 1,
            "text": "First page",
            "confidence": 91.25,
            "words": 2,
            "width": 2480,
            "height": 3508,
        },
        {
            "page": 2,
            "text": "Second page",
            "confidence": None,
            "words": 2,
            "width": 3508,
            "height": 2480,
        },
    ],
    "format": "PDF",
    "engine": "tesseract",
}


class FakePdfOcrService:
    """Capture PDF calls while keeping API tests independent of PDFium."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.thread_ids: list[int] = []
        self.error: Exception | None = None

    def recognize_pdf(self, **kwargs: Any) -> PdfOcrResponse:
        self.calls.append(kwargs)
        self.thread_ids.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        return PdfOcrResponse.model_validate(EXPECTED_PDF_RESPONSE)


@pytest.fixture
def fake_pdf_service(app: FastAPI) -> FakePdfOcrService:
    service = FakePdfOcrService()
    app.dependency_overrides[ocr_router.get_ocr_service] = lambda: service
    return service


async def test_pdf_recognize_returns_exact_json_contract(
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
) -> None:
    event_loop_thread_id = threading.get_ident()

    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={"file": ("document.pdf", b"pdf bytes", "application/pdf")},
        data={"preprocessing": "grayscale"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == EXPECTED_PDF_RESPONSE
    assert fake_pdf_service.thread_ids[0] != event_loop_thread_id
    assert fake_pdf_service.calls == [
        {
            "filename": "document.pdf",
            "data": b"pdf bytes",
            "content_type": "application/pdf",
            "language": "eng",
            "preprocessing": "grayscale",
            "threshold": None,
            "password": None,
        }
    ]


async def test_pdf_parameters_and_password_reach_service(
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
) -> None:
    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={
            "file": (
                "protected.pdf",
                b"protected pdf",
                "application/pdf; charset=binary",
            )
        },
        data={
            "language": "eng+rus",
            "preprocessing": "threshold",
            "threshold": "175",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    call = fake_pdf_service.calls[0]
    assert call["content_type"] == "application/pdf; charset=binary"
    assert call["language"] == "eng+rus"
    assert call["preprocessing"] == "threshold"
    assert call["threshold"] == 175
    assert call["password"] == "secret"


@pytest.mark.parametrize(
    ("data", "expected_fragment"),
    [
        ({"language": "ita"}, "language"),
        ({"preprocessing": "sharpen"}, "preprocessing"),
        ({"threshold": "-1"}, "threshold"),
        ({"threshold": "256"}, "threshold"),
    ],
)
async def test_invalid_pdf_form_values_return_422_without_calling_service(
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
    data: dict[str, str],
    expected_fragment: str,
) -> None:
    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={"file": ("document.pdf", b"pdf bytes", "application/pdf")},
        data=data,
    )

    assert response.status_code == 422
    assert expected_fragment in response.text
    assert fake_pdf_service.calls == []


async def test_empty_pdf_returns_400_without_calling_service(
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
) -> None:
    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={"file": ("document.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "The uploaded PDF is empty."}
    assert fake_pdf_service.calls == []


async def test_pdf_router_reads_only_limit_plus_one_and_returns_413(
    app: FastAPI,
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
) -> None:
    app.state.settings.max_pdf_bytes = 4

    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={"file": ("document.pdf", b"12345-extra-data", "application/pdf")},
    )

    assert response.status_code == 413
    assert "4-byte limit" in response.json()["detail"]
    assert fake_pdf_service.calls == []


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (EmptyPdfError(), 400),
        (InvalidPdfError(), 400),
        (PdfTooLargeError(), 413),
        (UnsupportedPdfFormatError(), 415),
        (InvalidOcrParametersError(), 422),
        (InvalidPdfPasswordError(), 422),
        (UnsupportedPdfSecurityError(), 422),
        (OcrEngineUnavailableError(), 503),
        (OcrTimeoutError(), 504),
        (PdfRenderError(), 500),
    ],
)
async def test_pdf_feature_errors_are_mapped_to_http_responses(
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
    error: Exception,
    status_code: int,
) -> None:
    fake_pdf_service.error = error

    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={"file": ("document.pdf", b"pdf bytes", "application/pdf")},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}


async def test_unexpected_pdf_error_returns_safe_500(
    client: AsyncClient,
    fake_pdf_service: FakePdfOcrService,
) -> None:
    fake_pdf_service.error = ValueError(r"C:\private\pdfium\secret.pdf")

    response = await client.post(
        "/api/ocr/pdf/recognize",
        files={"file": ("document.pdf", b"pdf bytes", "application/pdf")},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "OCR processing failed unexpectedly."}
    assert "private" not in response.text


async def test_openapi_contains_both_ocr_endpoints(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/ocr/recognize" in paths
    assert "/api/ocr/pdf/recognize" in paths
