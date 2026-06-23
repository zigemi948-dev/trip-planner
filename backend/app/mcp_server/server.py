from __future__ import annotations

import json
import sys
from typing import Any, Callable

from pydantic import ValidationError

from app.graph.state import FinancialContext, POICandidate
from app.mcp_server.amap_tools import distance_matrix_tool, hotel_anchor_tool, poi_search_tool, weather_constraints_tool
from app.mcp_server.finance_tools import finance_context_tool
from app.services.llm_service import complete_text


MCP_PROTOCOL_VERSION = "2024-11-05"


def _json_schema(schema_type: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": schema_type,
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "amap_poi_search": {
        "description": "Search Amap POIs and return solver-safe POI candidates.",
        "inputSchema": _json_schema(
            "object",
            {
                "city": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            ["city", "keywords"],
        ),
    },
    "amap_hotel_anchor": {
        "description": "Resolve a hotel anchor POI from Amap.",
        "inputSchema": _json_schema(
            "object",
            {"city": {"type": "string"}},
            ["city"],
        ),
    },
    "amap_distance_matrix": {
        "description": "Build time-dependent directed matrix edges for POI nodes.",
        "inputSchema": _json_schema(
            "object",
            {
                "nodes": {"type": "array", "items": {"type": "object"}},
                "financial": {"type": "object"},
            },
            ["nodes"],
        ),
    },
    "amap_weather_constraints": {
        "description": "Fetch Amap weather and return route solver constraints.",
        "inputSchema": _json_schema(
            "object",
            {"city": {"type": "string"}},
            ["city"],
        ),
    },
    "finance_context": {
        "description": "Return default financial assumptions used by the route solver.",
        "inputSchema": _json_schema("object", {}),
    },
    "llm_complete_text": {
        "description": "Call the configured OpenAI-compatible LLM and return text.",
        "inputSchema": _json_schema(
            "object",
            {
                "system_prompt": {"type": "string"},
                "user_prompt": {"type": "string"},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.2},
            },
            ["system_prompt", "user_prompt"],
        ),
    },
}


def _as_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _as_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    return value


def _tool_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(_as_json(value), ensure_ascii=False),
            }
        ]
    }


def _call_amap_poi_search(arguments: dict[str, Any]) -> Any:
    return poi_search_tool(
        city=str(arguments["city"]),
        keywords=list(arguments["keywords"]),
        limit=int(arguments.get("limit", 10)),
    )


def _call_amap_hotel_anchor(arguments: dict[str, Any]) -> Any:
    return hotel_anchor_tool(str(arguments["city"]))


def _call_amap_distance_matrix(arguments: dict[str, Any]) -> Any:
    nodes = [POICandidate.model_validate(item) for item in arguments["nodes"]]
    financial = FinancialContext.model_validate(arguments.get("financial") or {})
    return distance_matrix_tool(nodes, financial)


def _call_amap_weather_constraints(arguments: dict[str, Any]) -> Any:
    return weather_constraints_tool(str(arguments["city"]))


def _call_finance_context(arguments: dict[str, Any]) -> Any:
    return finance_context_tool()


def _call_llm_complete_text(arguments: dict[str, Any]) -> Any:
    return {
        "text": complete_text(
            system_prompt=str(arguments["system_prompt"]),
            user_prompt=str(arguments["user_prompt"]),
            temperature=float(arguments.get("temperature", 0.2)),
        )
    }


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "amap_poi_search": _call_amap_poi_search,
    "amap_hotel_anchor": _call_amap_hotel_anchor,
    "amap_distance_matrix": _call_amap_distance_matrix,
    "amap_weather_constraints": _call_amap_weather_constraints,
    "finance_context": _call_finance_context,
    "llm_complete_text": _call_llm_complete_text,
}


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC MCP message."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "trip-planner-mcp", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _response(
            request_id,
            {
                "tools": [
                    {"name": name, **schema}
                    for name, schema in TOOL_SCHEMAS.items()
                ]
            },
        )
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(str(name))
        if handler is None:
            return _error(request_id, -32602, f"Unknown tool: {name}")
        try:
            return _response(request_id, _tool_result(handler(arguments)))
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return _error(request_id, -32602, str(exc))
        except Exception as exc:
            return _error(request_id, -32000, str(exc))

    return _error(request_id, -32601, f"Unsupported method: {method}")


def main() -> None:
    """Run a line-delimited JSON-RPC MCP server over stdio."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle_message(message)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
