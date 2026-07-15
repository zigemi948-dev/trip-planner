import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.health_router import router as health_router
from app.api.http_router import router as http_router
from app.api.ws_router import router as ws_router
from app.core.config import settings

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    """Create the FastAPI application and register transport adapters.

    On startup, stale data (old jobs and exports) are cleaned automatically
    to prevent unbounded disk growth.
    """
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

    # Configure root logger so that agent fallback warnings appear in the
    # backend log.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # -----------------------------------------------------------------------
    # Startup lifecycle hooks — clean stale data without blocking the HTTP
    # server from becoming ready.
    # -----------------------------------------------------------------------
    @app.on_event("startup")
    async def _cleanup_on_startup() -> None:
        try:
            from app.services.job_service import job_store

            removed = job_store.cleanup_old_jobs()
            if removed:
                logger.info("Startup cleanup: removed %d stale job(s)", removed)
        except Exception:
            logger.warning("Startup cleanup: job cleanup failed (non-fatal)", exc_info=True)

        try:
            from app.services.export_service import cleanup_old_exports

            removed = cleanup_old_exports()
            if removed:
                logger.info("Startup cleanup: removed %d stale export(s)", removed)
        except Exception:
            logger.warning("Startup cleanup: export cleanup failed (non-fatal)", exc_info=True)

    return app


app = create_app()
