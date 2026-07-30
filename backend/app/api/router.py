"""Top-level API router."""

from fastapi import APIRouter

from app.features.health.router import router as health_router
from app.features.ocr.router import router as ocr_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ocr_router)
