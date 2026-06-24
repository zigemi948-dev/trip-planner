from fastapi import APIRouter

from app.core.config import settings
from app.graph.state import IntegrationProbeResponse, RuntimeCapabilities
from app.services.geo_fact_service import amap_mcp_enabled
from app.services.integration_probe import probe_integrations
from app.services.llm_service import llm_is_enabled

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Return a small liveness response for local dev and deployment probes."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }


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
