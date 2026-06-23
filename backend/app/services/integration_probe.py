from __future__ import annotations

from app.graph.state import IntegrationProbeResponse, IntegrationProbeResult
from app.services.amap_service import AmapUnavailableError, amap_is_enabled, search_pois
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
    if not amap_is_enabled():
        return IntegrationProbeResult(
            name="amap",
            status="skipped",
            enabled=False,
            message="Amap provider is not enabled.",
        )
    try:
        pois = search_pois("Shanghai", ["museum"], limit=1)
    except AmapUnavailableError as exc:
        return IntegrationProbeResult(
            name="amap",
            status="error",
            enabled=True,
            message=str(exc),
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
            message=str(exc),
        )
    return IntegrationProbeResult(
        name="llm",
        status="ok",
        enabled=True,
        message=f"Received {len(text)} character response.",
    )
