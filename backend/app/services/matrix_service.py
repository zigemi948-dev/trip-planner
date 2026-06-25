from __future__ import annotations

from hashlib import sha1

from app.algorithms.matrix_builder import build_fallback_matrix
from app.core.config import settings
from app.graph.state import FinancialContext, MatrixEdge, POICandidate
from app.services.amap_service import AmapUnavailableError, amap_is_enabled, build_amap_matrix
from app.services.cache_service import MemoryCache
from app.services.geo_fact_service import GeoFactUnavailableError, build_time_dependent_matrix_facts


MatrixBuild = tuple[dict[str, MatrixEdge], str]

matrix_cache: MemoryCache[MatrixBuild] = MemoryCache()


def _cache_key(nodes: list[POICandidate], financial: FinancialContext, city: str = "") -> str:
    """Build a stable cache key from node IDs, coordinates, and cost settings."""
    raw = "|".join(
        [
            *[
                f"{node.id}:{node.coordinates.lat:.6f},{node.coordinates.lng:.6f}"
                for node in nodes
            ],
            f"transit={financial.base_transit_fare}",
            f"driving={financial.driving_rate_per_km}",
            f"provider={settings.provider_mode}",
            f"city={city}",
        ]
    )
    return sha1(raw.encode("utf-8")).hexdigest()


def build_time_dependent_matrix(
    nodes: list[POICandidate],
    financial: FinancialContext,
    city: str = "",
) -> dict[str, MatrixEdge]:
    """Build or reuse the route matrix.

    Second-stage behavior still falls back to Haversine, but the service now has
    the cache and provider boundary needed for real Amap batch calls later.
    """
    matrix, _ = build_time_dependent_matrix_with_source(nodes, financial, city)
    return matrix


def build_time_dependent_matrix_with_source(
    nodes: list[POICandidate],
    financial: FinancialContext,
    city: str = "",
) -> MatrixBuild:
    """Build or reuse the route matrix and report the selected source."""
    key = _cache_key(nodes, financial, city)
    cached = matrix_cache.get(key)
    if cached is not None:
        matrix, source = cached
        return matrix, f"cache:{source}"

    if settings.provider_mode.lower() == "amap":
        try:
            if amap_is_enabled():
                matrix = build_amap_matrix(nodes, financial)
                source = "amap:rest"
            else:
                try:
                    matrix = build_time_dependent_matrix_facts(nodes, financial, city)
                except TypeError:
                    matrix = build_time_dependent_matrix_facts(nodes, financial)
                source = "amap:mcp"
        except (AmapUnavailableError, GeoFactUnavailableError):
            matrix = build_fallback_matrix(nodes, financial)
            source = "fallback"
    else:
        matrix = build_fallback_matrix(nodes, financial)
        source = "fallback"
    matrix_cache.set(key, (matrix, source), ttl_seconds=settings.matrix_cache_ttl_seconds)
    return matrix, source
