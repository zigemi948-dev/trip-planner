from __future__ import annotations

from app.graph.state import IntegrationProbeResponse, IntegrationProbeResult
from app.core.config import settings
from app.services.geo_fact_service import GeoFactUnavailableError, amap_mcp_enabled, search_poi_facts
from app.services.llm_service import LLMUnavailableError, complete_text, llm_is_enabled


def probe_integrations() -> IntegrationProbeResponse:
    """Run explicit smoke probes for optional remote integrations."""
    return IntegrationProbeResponse(
        results=[
            _probe_amap(),
            _probe_llm(),
        ]
    )


def _probe_amap() -> IntegrationProbeResult:
    if not amap_mcp_enabled():
        return IntegrationProbeResult(
            name="amap",
            status="skipped",
            enabled=False,
            message="Amap MCP provider is not enabled.",
        )
    try:
        pois = search_poi_facts("Shanghai", ["museum"], limit=1)
    except GeoFactUnavailableError as exc:
        return IntegrationProbeResult(
            name="amap",
            status="error",
            enabled=True,
            message=_probe_error_message(str(exc)),
        )
    return IntegrationProbeResult(
        name="amap",
        status="ok",
        enabled=True,
        message=f"Fetched {len(pois)} POI candidate(s).",
    )


def _probe_llm() -> IntegrationProbeResult:
    if not llm_is_enabled():
        return IntegrationProbeResult(
            name="llm",
            status="skipped",
            enabled=False,
            message="LLM is not enabled.",
        )
    try:
        text = complete_text(
            "Reply with the exact token OK.",
            "Connectivity probe.",
            temperature=0.0,
        )
    except LLMUnavailableError as exc:
        return IntegrationProbeResult(
            name="llm",
            status="error",
            enabled=True,
            message=_probe_error_message(str(exc)),
        )
    return IntegrationProbeResult(
        name="llm",
        status="ok",
        enabled=True,
        message=f"Received {len(text)} character response.",
    )


def _probe_error_message(message: str) -> str:
    if "TRIP_SSL_VERIFY=false" in message:
        return message
    if not settings.ssl_verify:
        return f"{message}. SSL verification is disabled for local development."
    return message
