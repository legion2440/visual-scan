"""Production ASGI entry point for Uvicorn."""

from app.factory import create_app

app = create_app()
