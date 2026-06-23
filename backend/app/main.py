from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.health_router import router as health_router
from app.api.http_router import router as http_router
from app.api.ws_router import router as ws_router
from app.core.config import settings


def create_app() -> FastAPI:
    """Create the FastAPI application and register transport adapters."""
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(http_router, prefix="/api")
    app.include_router(ws_router)
    return app


app = create_app()
