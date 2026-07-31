"""Side-effect-free FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.features.analysis.router import (
    initialize_analysis_state,
    shutdown_analysis_service,
)
from app.features.scans.service import create_scans_service


@asynccontextmanager
async def application_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize app-local feature state and release lazy resources."""
    initialize_analysis_state(application)
    try:
        scans_service = create_scans_service(application.state.settings)
        application.state.scans_service = scans_service
        await run_in_threadpool(scans_service.bootstrap)
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=app_settings.api_prefix)
    return application
