from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.mcp_server.server import handle_message


app = FastAPI(title="Trip Planner MCP Server", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
def mcp_json_rpc(message: dict[str, Any]) -> dict[str, Any]:
    """Serve the same MCP JSON-RPC handler over HTTP for local development."""
    response = handle_message(message)
    return response or {"jsonrpc": "2.0", "result": None}
