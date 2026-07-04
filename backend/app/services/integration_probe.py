from __future__ import annotations

from pydantic import BaseModel

from app.graph.state import IntegrationProbeResponse, IntegrationProbeResult
from app.core.config import settings
from app.services.geo_fact_service import GeoFactUnavailableError, amap_mcp_enabled, search_poi_facts
from app.services.http_client import (
    amap_circuit_breaker,
    llm_circuit_breaker,
    mcp_circuit_breaker,
)
from app.services.llm_service import LLMUnavailableError, complete_text, llm_is_enabled


class ProbeResult(BaseModel):
    """Individual circuit breaker probe result."""

    name: str
    state: str  # "closed" | "open" | "half-open"
    failure_count: int
    last_failure_age_seconds: float | None


def probe_circuit_breakers() -> list[ProbeResult]:
    """Return the current state of all circuit breakers."""
    now = __import__("time").time()
    results: list[ProbeResult] = []
    for name, cb in (
        ("amap", amap_circuit_breaker),
        ("mcp", mcp_circuit_breaker),
        ("llm", llm_circuit_breaker),
    ):
        last_failure = cb.last_failure_time
        age = (now - last_failure) if last_failure and cb.state == "open" else None
        results.append(
            ProbeResult(
                name=name,
                state=cb.state,
                failure_count=cb.failure_count,
                last_failure_age_seconds=round(age, 2) if age is not None else None,
            )
        )
    return results


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
