import os
from pathlib import Path

from pydantic import BaseModel, Field

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


def _load_dotenv_file(path: Path) -> None:
    """Load simple KEY=VALUE lines without overriding real environment vars."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


for dotenv_path in (PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"):
    _load_dotenv_file(dotenv_path)


class Settings(BaseModel):
    """Application settings loaded from environment variables.

    This avoids making the local demo script depend on `pydantic-settings`.
    FastAPI deployments can still configure values with the TRIP_ prefix.
    """

    app_name: str = "Trip Planner"
    app_version: str = "0.1.0"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    solver_timeout_ms: int = 1200
    city_detour_factor: float = 1.4
    max_day_minutes: int = 600
    provider_mode: str = "local"
    amap_api_key: str = ""
    amap_base_url: str = "https://restapi.amap.com/v3"
    amap_timeout_seconds: int = 8
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1/chat/completions"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 20
    ssl_verify: bool = True
    ssl_ca_file: str = ""
    mcp_http_url: str = ""
    mcp_timeout_seconds: int = 20
    mcp_allow_inprocess: bool = False
    amap_mcp_poi_tool: str = "amap_poi_search"
    amap_mcp_hotel_tool: str = "amap_hotel_anchor"
    amap_mcp_weather_tool: str = "amap_weather_constraints"
    amap_mcp_matrix_tool: str = "amap_distance_matrix"
    matrix_cache_ttl_seconds: int = 3600
    job_store_path: str = "data/jobs.jsonl"
    unsplash_access_key: str = ""


def resolve_backend_path(path: str | Path) -> Path:
    """Resolve app-owned runtime paths consistently from repo or backend cwd."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    if candidate.parts and candidate.parts[0] == "backend":
        return PROJECT_ROOT / candidate
    return BACKEND_ROOT / candidate


def _csv_env(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


settings = Settings(
    app_name=os.getenv("TRIP_APP_NAME", "Trip Planner"),
    app_version=os.getenv("TRIP_APP_VERSION", "0.1.0"),
    cors_origins=_csv_env("TRIP_CORS_ORIGINS", ["http://localhost:5173"]),
    solver_timeout_ms=int(os.getenv("TRIP_SOLVER_TIMEOUT_MS", "1200")),
    city_detour_factor=float(os.getenv("TRIP_CITY_DETOUR_FACTOR", "1.4")),
    max_day_minutes=int(os.getenv("TRIP_MAX_DAY_MINUTES", "600")),
    provider_mode=os.getenv("TRIP_PROVIDER_MODE", "local"),
    amap_api_key=os.getenv("TRIP_AMAP_API_KEY", ""),
    amap_base_url=os.getenv("TRIP_AMAP_BASE_URL", "https://restapi.amap.com/v3"),
    amap_timeout_seconds=int(os.getenv("TRIP_AMAP_TIMEOUT_SECONDS", "8")),
    llm_enabled=_bool_env("TRIP_LLM_ENABLED", False),
    llm_api_key=os.getenv("TRIP_LLM_API_KEY", ""),
    llm_base_url=os.getenv("TRIP_LLM_BASE_URL", "https://api.openai.com/v1/chat/completions"),
    llm_model=os.getenv("TRIP_LLM_MODEL", "gpt-4o-mini"),
    llm_timeout_seconds=int(os.getenv("TRIP_LLM_TIMEOUT_SECONDS", "20")),
    ssl_verify=_bool_env("TRIP_SSL_VERIFY", True),
    ssl_ca_file=os.getenv("TRIP_SSL_CA_FILE", ""),
    mcp_http_url=os.getenv("TRIP_MCP_HTTP_URL", ""),
    mcp_timeout_seconds=int(os.getenv("TRIP_MCP_TIMEOUT_SECONDS", "20")),
    mcp_allow_inprocess=_bool_env("TRIP_MCP_ALLOW_INPROCESS", False),
    amap_mcp_poi_tool=os.getenv("TRIP_AMAP_MCP_POI_TOOL", "amap_poi_search"),
    amap_mcp_hotel_tool=os.getenv("TRIP_AMAP_MCP_HOTEL_TOOL", "amap_hotel_anchor"),
    amap_mcp_weather_tool=os.getenv("TRIP_AMAP_MCP_WEATHER_TOOL", "amap_weather_constraints"),
    amap_mcp_matrix_tool=os.getenv("TRIP_AMAP_MCP_MATRIX_TOOL", "amap_distance_matrix"),
    matrix_cache_ttl_seconds=int(os.getenv("TRIP_MATRIX_CACHE_TTL_SECONDS", "3600")),
    job_store_path=os.getenv("TRIP_JOB_STORE_PATH", "data/jobs.jsonl"),
    unsplash_access_key=os.getenv("UNSPLASH_ACCESS_KEY", "")
)
