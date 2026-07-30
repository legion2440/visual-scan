"""Health HTTP endpoint."""

from fastapi import APIRouter

from app.features.health.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Report backend reachability and currently unavailable AI support."""
    return HealthResponse()
