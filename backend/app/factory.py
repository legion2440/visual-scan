"""Side-effect-free FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.features.analysis.router import (
    initialize_analysis_state,
    shutdown_analysis_service,
)
from app.features.auth.service import create_auth_service
from app.features.scans.service import create_scans_service
from app.storage.database import SQLiteDatabase


async def safe_request_validation_handler(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    """Return validation details without reflecting unsafe request values."""
    details = [
        {key: value for key, value in item.items() if key in {"type", "loc", "msg"}}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})


@asynccontextmanager
async def application_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize app-local feature state and release lazy resources."""
    initialize_analysis_state(application)
    try:
        settings = application.state.settings
        database = SQLiteDatabase(
            database_path=settings.scans_database_path,
            busy_timeout_ms=settings.scans_database_busy_timeout_ms,
        )
        application.state.database = database
        application.state.auth_service = create_auth_service(database, settings)
        application.state.scans_service = create_scans_service(database, settings)
        await run_in_threadpool(database.bootstrap)
        yield
    finally:
        await shutdown_analysis_service(application)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a configured FastAPI application without performing heavy work."""
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=application_lifespan,
    )
    application.state.settings = app_settings
    application.add_exception_handler(
        RequestValidationError,
        safe_request_validation_handler,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    application.include_router(api_router, prefix=app_settings.api_prefix)
    return application
