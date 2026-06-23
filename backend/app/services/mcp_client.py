from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import settings


class MCPToolError(RuntimeError):
    """Raised when a JSON-RPC MCP tool call fails."""


def call_tool(name: str, arguments: dict) -> object:
    """Call a registered MCP tool over HTTP or the in-process JSON-RPC handler."""
    message = {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    }
    response = _call_http(message) if settings.mcp_http_url else _call_in_process(message)
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
    url = settings.mcp_http_url.rstrip("/")
    if not url.endswith("/mcp"):
        url = f"{url}/mcp"
    request = Request(
        url,
        data=json.dumps(message).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.mcp_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise MCPToolError(str(exc)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MCPToolError("MCP response was not valid JSON") from exc


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
