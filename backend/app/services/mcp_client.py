from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

import logging

from app.core.config import settings
from app.services.http_client import (
    CircuitBreakerOpenError,
    explain_network_error,
    mcp_circuit_breaker,
    open_url,
    retry_on_error,
)


logger = logging.getLogger(__name__)


class MCPToolError(RuntimeError):
    """Raised when a JSON-RPC MCP tool call fails."""


def call_tool(name: str, arguments: dict) -> object:
    """Call a registered MCP tool over an explicitly configured transport."""
    message = {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    }
    if settings.mcp_http_url:
        response = _call_http(message)
    elif settings.mcp_allow_inprocess:
        response = _call_in_process(message)
    else:
        raise MCPToolError(
            "External MCP endpoint is not configured. Set TRIP_MCP_HTTP_URL, "
            "or set TRIP_MCP_ALLOW_INPROCESS=true only for local development."
        )
    if "error" in response:
        error = response["error"]
        raise MCPToolError(str(error.get("message") or error))
    return _extract_tool_payload(response.get("result") or {})


def _call_in_process(message: dict) -> dict:
    from app.mcp_server.server import handle_message

    response = handle_message(message)
    if response is None:
        raise MCPToolError("MCP tool call produced no response")
    return response


def _call_http(message: dict) -> dict:
    url = _mcp_endpoint_url(settings.mcp_http_url)

    def _do_request() -> dict:
        request = Request(
            url,
            data=json.dumps(message).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        try:
            with open_url(request, timeout=settings.mcp_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise MCPToolError(explain_network_error(exc)) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPToolError("MCP response was not valid JSON") from exc

    try:
        return retry_on_error(
            _do_request,
            max_attempts=settings.amap_retry_max_attempts,
            base_delay_ms=settings.amap_retry_base_delay_ms,
            circuit_breaker=mcp_circuit_breaker,
            logger_name="mcp_client",
        )
    except CircuitBreakerOpenError as exc:
        logger.warning("MCP circuit breaker open; skipping tool call")
        raise MCPToolError(str(exc)) from exc


def _mcp_endpoint_url(raw_url: str) -> str:
    """Append /mcp without corrupting query-string API keys."""
    parts = urlsplit(raw_url.strip())
    path = parts.path.rstrip("/")
    if not path.endswith("/mcp"):
        path = f"{path}/mcp"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _extract_tool_payload(result: dict) -> object:
    content = result.get("content") or []
    if not content:
        return None
    text = content[0].get("text")
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text