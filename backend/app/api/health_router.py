import logging

from fastapi import APIRouter

from app.core.config import settings
from app.graph.state import IntegrationProbeResponse, RuntimeCapabilities
from app.services.export_service import cleanup_old_exports
from app.services.geo_fact_service import amap_mcp_enabled
from app.services.integration_probe import ProbeResult, probe_integrations, probe_circuit_breakers
from app.services.job_service import job_store
from app.services.llm_service import llm_is_enabled

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Return a small liveness response for local dev and deployment probes."""
    result: dict[str, str] = {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
    if job_store.persistence_error:
        result["job_store_warning"] = job_store.persistence_error
        logger.warning("Health check: job store persistence error: %s", job_store.persistence_error)
    return result


@router.get("/health/capabilities", response_model=RuntimeCapabilities)
def runtime_capabilities() -> RuntimeCapabilities:
    """Return runtime integration switches without exposing secrets."""
    amap_mcp_configured = bool(settings.mcp_http_url) or settings.mcp_allow_inprocess
    llm_configured = bool(settings.llm_api_key)
    return RuntimeCapabilities(
        provider_mode=settings.provider_mode,
        amap_configured=amap_mcp_configured,
        amap_enabled=amap_mcp_enabled() and amap_mcp_configured,
        amap_mcp_configured=amap_mcp_configured,
        mcp_inprocess_allowed=settings.mcp_allow_inprocess,
        llm_configured=llm_configured,
        llm_enabled=llm_is_enabled(),
        llm_model=settings.llm_model,
        fallback_mode=not (amap_mcp_enabled() and amap_mcp_configured),
    )


@router.get("/health/integrations/probe", response_model=IntegrationProbeResponse)
def integration_probe() -> IntegrationProbeResponse:
    """Run manual smoke probes for optional remote integrations."""
    return probe_integrations()


@router.get("/health/job-store")
def job_store_health() -> dict[str, str]:
    """Return job store persistence status."""
    return {
        "persistence_error": job_store.persistence_error or "",
        "job_count": str(len(job_store.list())),
    }


@router.get("/health/circuit-breaker", response_model=list[ProbeResult])
def circuit_breaker_status() -> list[ProbeResult]:
    """Return circuit breaker states for all external integrations."""
    return probe_circuit_breakers()


# ---------------------------------------------------------------------------
# Data lifecycle management endpoints
# ---------------------------------------------------------------------------

@router.post("/health/cleanup/jobs")
def cleanup_jobs() -> dict[str, int]:
    """Clean up old or excessive planning jobs.

    Returns the number of removed jobs.
    """
    removed = job_store.cleanup_old_jobs()
    return {"removed": removed}


@router.post("/health/cleanup/exports")
def cleanup_exports() -> dict[str, int]:
    """Remove export artifacts exceeding age or count limits.

    Returns the number of removed files.
    """
    removed = cleanup_old_exports()
    return {"removed": removed}