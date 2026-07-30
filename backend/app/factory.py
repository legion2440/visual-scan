"""Side-effect-free FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a configured FastAPI application without performing heavy work."""
    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
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
