"""Health feature contracts."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Public response returned by GET /api/health."""

    status: Literal["ok"] = "ok"
    ai_available: bool = False
    provider: str | None = None
