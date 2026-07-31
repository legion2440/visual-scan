"""Top-level API router."""

from fastapi import APIRouter

from app.features.analysis.router import router as analysis_router
from app.features.health.router import router as health_router
from app.features.ocr.router import router as ocr_router
from app.features.scans.router import router as scans_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ocr_router)
api_router.include_router(analysis_router)
api_router.include_router(scans_router)
