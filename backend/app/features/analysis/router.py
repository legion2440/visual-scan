"""HTTP transport and app-local lifecycle for document analysis."""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request

from app.core.config import Settings
from app.features.analysis.errors import (
    AnalysisDisabledError,
    AnalysisError,
)
from app.features.analysis.pipeline import AnalysisPipeline
from app.features.analysis.provider import OpenAICompatibleProvider
from app.features.analysis.schemas import AnalysisRequest, AnalysisResponse
from app.features.analysis.service import AnalysisService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["analysis"])

_SERVICE_ATTRIBUTE = "_visual_scan_analysis_service"
_LOCK_ATTRIBUTE = "_visual_scan_analysis_lock"


def initialize_analysis_state(application: FastAPI) -> None:
    """Create app-local synchronization without constructing an HTTP client."""
    setattr(application.state, _LOCK_ATTRIBUTE, asyncio.Lock())


def _build_analysis_service(settings: Settings) -> AnalysisService:
    client = httpx.AsyncClient()
    provider = OpenAICompatibleProvider(
        client=client,
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_output_tokens=settings.ai_max_output_tokens,
        response_format=settings.ai_response_format,
    )
    return AnalysisService(
        AnalysisPipeline(provider),
        max_input_chars=settings.ai_max_input_chars,
        provider_name=settings.ai_provider_name,
    )


async def get_analysis_service(request: Request) -> AnalysisService:
    """Return one lazy service and client for this application instance."""
    settings = request.app.state.settings
    if not settings.ai_enabled:
        raise AnalysisDisabledError()

    service = getattr(request.app.state, _SERVICE_ATTRIBUTE, None)
    if service is not None:
        return service

    lock = getattr(request.app.state, _LOCK_ATTRIBUTE, None)
    if lock is None:
        raise RuntimeError("Application lifespan has not initialized analysis state.")
    async with lock:
        service = getattr(request.app.state, _SERVICE_ATTRIBUTE, None)
        if service is None:
            service = _build_analysis_service(settings)
            setattr(request.app.state, _SERVICE_ATTRIBUTE, service)
    return service


async def shutdown_analysis_service(application: FastAPI) -> None:
    """Close a created service once; leave untouched lazy state alone."""
    service = getattr(application.state, _SERVICE_ATTRIBUTE, None)
    if service is None:
        return
    delattr(application.state, _SERVICE_ATTRIBUTE)
    await service.close()


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_document(request: Request, payload: AnalysisRequest) -> AnalysisResponse:
    """Classify and summarize OCR text through the configured provider."""
    try:
        service = await get_analysis_service(request)
        return await service.analyze(
            filename=payload.filename,
            text=payload.text,
            language=payload.language,
        )
    except AnalysisError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except Exception as error:
        logger.error(
            "Unexpected AI analysis request failure (%s)",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=AnalysisError.default_message,
        ) from error
