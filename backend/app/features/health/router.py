"""Health HTTP endpoint."""

from fastapi import APIRouter, Request

from app.features.health.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    """Report backend reachability and configured AI availability."""
    settings = request.app.state.settings
    return HealthResponse(
        ai_available=settings.ai_enabled,
        provider=settings.ai_provider_name if settings.ai_enabled else None,
    )
