"""HTTP transport for server-side OCR."""

from __future__ import annotations

import logging
from contextlib import suppress
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.features.auth.dependencies import require_csrf_principal
from app.features.auth.schemas import AuthenticatedPrincipal
from app.features.ocr.errors import (
    EmptyImageError,
    EmptyPdfError,
    ImageTooLargeError,
    OcrError,
    PdfTooLargeError,
)
from app.features.ocr.pdf_pipeline import PdfOcrPipeline
from app.features.ocr.pdf_renderer import PdfiumRenderer
from app.features.ocr.pipeline import OcrPipeline
from app.features.ocr.provider import TesseractProvider
from app.features.ocr.schemas import (
    OcrLanguage,
    OcrResponse,
    PdfOcrResponse,
    PreprocessingMode,
)
from app.features.ocr.service import OcrService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ocr", tags=["ocr"])

_SERVICE_ATTRIBUTE = "_visual_scan_ocr_service"
_SERVICE_LOCK = Lock()


def _build_ocr_service(settings: Settings) -> OcrService:
    provider = TesseractProvider(
        timeout_seconds=settings.ocr_timeout_seconds,
        tesseract_command=settings.tesseract_cmd,
    )
    image_pipeline = OcrPipeline(
        provider=provider,
        max_image_pixels=settings.max_image_pixels,
    )
    pdf_pipeline = PdfOcrPipeline(
        renderer=PdfiumRenderer(render_dpi=settings.pdf_render_dpi),
        provider=provider,
        max_pages=settings.max_pdf_pages,
        max_page_pixels=settings.max_pdf_page_pixels,
        max_total_pixels=settings.max_pdf_total_pixels,
        ocr_timeout_seconds=settings.ocr_timeout_seconds,
        pdf_timeout_seconds=settings.pdf_timeout_seconds,
    )
    return OcrService(
        image_pipeline=image_pipeline,
        pdf_pipeline=pdf_pipeline,
        max_image_bytes=settings.max_image_bytes,
        max_pdf_bytes=settings.max_pdf_bytes,
    )


def get_ocr_service(request: Request) -> OcrService:
    """Return the app-local lazy OCR singleton using injected app settings."""
    service = getattr(request.app.state, _SERVICE_ATTRIBUTE, None)
    if service is not None:
        return service

    with _SERVICE_LOCK:
        service = getattr(request.app.state, _SERVICE_ATTRIBUTE, None)
        if service is None:
            service = _build_ocr_service(request.app.state.settings)
            setattr(request.app.state, _SERVICE_ATTRIBUTE, service)
    return service


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    try:
        return await file.read(max_bytes + 1)
    except Exception as error:
        logger.exception("Failed to read an OCR upload")
        raise OcrError() from error
    finally:
        with suppress(Exception):
            await file.close()


@router.post("/recognize", response_model=OcrResponse)
async def recognize_image(
    request: Request,
    file: Annotated[UploadFile, File()],
    service: Annotated[OcrService, Depends(get_ocr_service)],
    _principal: Annotated[AuthenticatedPrincipal, Depends(require_csrf_principal)],
    language: Annotated[OcrLanguage, Form()] = OcrLanguage.ENGLISH,
    preprocessing: Annotated[PreprocessingMode, Form()] = PreprocessingMode.NONE,
    threshold: Annotated[int | None, Form(ge=0, le=255)] = None,
) -> OcrResponse:
    """Recognize one multipart image without persisting the original."""
    max_bytes = request.app.state.settings.max_image_bytes

    try:
        data = await _read_upload(file, max_bytes)
        if not data:
            raise EmptyImageError()
        if len(data) > max_bytes:
            raise ImageTooLargeError(f"The uploaded image exceeds the {max_bytes}-byte limit.")
        return await run_in_threadpool(
            service.recognize,
            filename=file.filename,
            data=data,
            content_type=file.content_type,
            language=language,
            preprocessing=preprocessing,
            threshold=threshold,
        )
    except OcrError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error
    except Exception as error:
        logger.exception("Unexpected OCR request failure")
        raise HTTPException(
            status_code=500,
            detail=OcrError.default_message,
        ) from error


@router.post("/pdf/recognize", response_model=PdfOcrResponse)
async def recognize_pdf(
    request: Request,
    file: Annotated[UploadFile, File()],
    service: Annotated[OcrService, Depends(get_ocr_service)],
    _principal: Annotated[AuthenticatedPrincipal, Depends(require_csrf_principal)],
    language: Annotated[OcrLanguage, Form()] = OcrLanguage.ENGLISH,
    preprocessing: Annotated[PreprocessingMode, Form()] = PreprocessingMode.NONE,
    threshold: Annotated[int | None, Form(ge=0, le=255)] = None,
    password: Annotated[str | None, Form()] = None,
) -> PdfOcrResponse:
    """Recognize a multipart PDF without persisting the original."""
    max_bytes = request.app.state.settings.max_pdf_bytes

    try:
        data = await _read_upload(file, max_bytes)
        if not data:
            raise EmptyPdfError()
        if len(data) > max_bytes:
            raise PdfTooLargeError(f"The uploaded PDF exceeds the {max_bytes}-byte limit.")
        return await run_in_threadpool(
            service.recognize_pdf,
            filename=file.filename,
            data=data,
            content_type=file.content_type,
            language=language,
            preprocessing=preprocessing,
            threshold=threshold,
            password=password,
        )
    except OcrError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
        ) from error
    except Exception as error:
        logger.exception("Unexpected PDF OCR request failure")
        raise HTTPException(
            status_code=500,
            detail=OcrError.default_message,
        ) from error
