"""Test the Trip Planner MCP JSON-RPC server over HTTP.

This is a pure network-layer test suite (版本 A).
It sends JSON-RPC messages to the MCP HTTP server and validates responses,
without importing any backend modules directly.
"""

from __future__ import annotations

import json
import time
from typing import Any
import urllib.error
import urllib.request

import pytest


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MCP_BASE = "http://127.0.0.1:8765"
MCP_ENDPOINT = f"{MCP_BASE}/mcp"
MCP_HEALTH = f"{MCP_BASE}/health"

TIMEOUT_SECONDS = 15
AMAP_QPS_DELAY = 0.3  # seconds between Amap API calls to stay under 5 QPS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonrpc(method: str, params: dict[str, Any] | None = None, request_id: int = 1) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 request body."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    """POST a JSON-RPC message to the MCP server and return the parsed response."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        MCP_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.fail(f"MCP server unreachable at {MCP_ENDPOINT}: {exc}")


def _get(path: str) -> dict[str, Any]:
    """GET a plain HTTP endpoint and return parsed JSON."""
    req = urllib.request.Request(path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.fail(f"GET {path} failed: {exc}")


def _assert_ok(response: dict[str, Any]) -> dict[str, Any]:
    """Assert no error in the JSON-RPC response and return the result."""
    assert "error" not in response, (
        f"Unexpected error: {response.get('error')}"
    )
    assert "result" in response, "Response missing 'result' field"
    return response["result"]


def _assert_error(response: dict[str, Any], code: int) -> dict[str, Any]:
    """Assert the response contains an error with the expected code."""
    assert "error" in response, "Expected an error response"
    assert response["error"]["code"] == code, (
        f"Expected error code {code}, got {response['error']['code']}: "
        f"{response['error'].get('message')}"
    )
    return response["error"]


# ===================================================================
# Tests
# ===================================================================


class TestHealth:
    """Plain HTTP health endpoint (not JSON-RPC)."""

    def test_health_endpoint(self) -> None:
        result = _get(MCP_HEALTH)
        assert result == {"status": "ok"}, f"Unexpected health response: {result}"

    def test_root_endpoint(self) -> None:
        """GET / should return server info (provided by the stdlib HTTP server)."""
        req = urllib.request.Request(MCP_BASE, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            pytest.fail(f"GET {MCP_BASE} failed: {exc}")
        assert data.get("status") == "ok"
        assert "name" in data


class TestInitialize:
    """MCP initialize handshake."""

    def test_initialize(self) -> None:
        payload = _jsonrpc("initialize", request_id=1)
        result = _assert_ok(_post(payload))
        assert "protocolVersion" in result
        assert "serverInfo" in result
        assert result["serverInfo"]["name"] == "trip-planner-mcp"

    def test_notifications_initialized(self) -> None:
        """notifications/initialized should return None (idempotent)."""
        payload = _jsonrpc("notifications/initialized", request_id=2)
        response = _post(payload)
        # The handler returns None, which the HTTP server converts to
        # {"jsonrpc": "2.0", "result": None}
        assert response.get("result") is None


class TestToolsList:
    """MCP tools/list — discover all available tools."""

    TOOL_NAMES = {
        "amap_poi_search",
        "amap_hotel_anchor",
        "amap_distance_matrix",
        "amap_weather_constraints",
        "finance_context",
        "llm_complete_text",
    }

    def test_list_tools(self) -> None:
        payload = _jsonrpc("tools/list", request_id=10)
        result = _assert_ok(_post(payload))
        tools = result.get("tools", [])
        assert len(tools) >= 6, f"Expected at least 6 tools, got {len(tools)}"

        tool_names = {t["name"] for t in tools}
        assert tool_names == self.TOOL_NAMES, (
            f"Tool name mismatch.\n"
            f"  Expected: {self.TOOL_NAMES}\n"
            f"  Got:      {tool_names}"
        )

        # Verify each tool has required schema fields
        for tool in tools:
            assert "description" in tool, f"Tool {tool['name']} missing description"
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"


class TestFinanceContext:
    """tools/call: finance_context (zero arguments)."""

    def test_finance_context(self) -> None:
        payload = _jsonrpc("tools/call", {"name": "finance_context", "arguments": {}}, request_id=20)
        result = _assert_ok(_post(payload))
        content = result.get("content", [])
        assert len(content) == 1
        text = json.loads(content[0]["text"])
        assert text["currency"] == "CNY"
        assert text["exchange_rate"] == 1.0
        assert text["base_transit_fare"] == 4.0
        assert text["driving_rate_per_km"] == 2.6
        assert "avg_meal_cost" in text
        assert "avg_hotel_nightly_cost" in text


class TestPOISearch:
    """tools/call: amap_poi_search."""

    def test_poi_search_basic(self) -> None:
        """Search with one city and one keyword."""
        time.sleep(AMAP_QPS_DELAY)
        payload = _jsonrpc(
            "tools/call",
            {
                "name": "amap_poi_search",
                "arguments": {"city": "长沙", "keywords": ["景点"], "limit": 3},
            },
            request_id=30,
        )
        result = _assert_ok(_post(payload))
        content = result.get("content", [])
        assert len(content) == 1
        pois = json.loads(content[0]["text"])
        assert len(pois) >= 1, "Expected at least 1 POI"
        poi = pois[0]
        assert "id" in poi
        assert "name" in poi
        assert "category" in poi
        assert "coordinates" in poi
        assert "lat" in poi["coordinates"]
        assert "lng" in poi["coordinates"]
        assert "fixed_cost" in poi
        assert "visit_duration_minutes" in poi
        assert "utility" in poi

    def test_poi_search_multiple_keywords(self) -> None:
        """Search with multiple keywords."""
        time.sleep(AMAP_QPS_DELAY)
        payload = _jsonrpc(
            "tools/call",
            {
                "name": "amap_poi_search",
                "arguments": {"city": "长沙", "keywords": ["景点", "美食"], "limit": 5},
            },
            request_id=31,
        )
        result = _assert_ok(_post(payload))
        content = result.get("content", [])
        pois = json.loads(content[0]["text"])
        assert len(pois) >= 1

    def test_poi_search_no_results(self) -> None:
        """Search with gibberish — should still return valid JSON (could be empty or error)."""
        time.sleep(AMAP_QPS_DELAY)
        payload = _jsonrpc(
            "tools/call",
            {
                "name": "amap_poi_search",
                "arguments": {"city": "长沙", "keywords": ["zzzznotexist12345"], "limit": 3},
            },
            request_id=32,
        )
        response = _post(payload)
        # If Amap returns nothing, the tool may raise AmapUnavailableError -> -32000
        # That is acceptable — we just verify the response shape is valid JSON-RPC.
        if "error" in response:
            assert response["error"]["code"] in (-32000, -32602)
        else:
            assert "result" in response


class TestHotelAnchor:
    """tools/call: amap_hotel_anchor."""

    def test_hotel_anchor(self) -> None:
        """Resolve a hotel anchor — skip if Amap QPS limit is hit."""
        time.sleep(AMAP_QPS_DELAY)
        payload = _jsonrpc(
            "tools/call",
            {"name": "amap_hotel_anchor", "arguments": {"city": "长沙"}},
            request_id=40,
        )
        response = _post(payload)
        if "error" in response:
            # Amap QPS limit or unavailable — acceptable, skip the test
            error_code = response["error"]["code"]
            error_msg = response["error"].get("message", "")
            if error_code == -32000 and "CUQPS" in error_msg:
                pytest.skip(f"高德 API QPS 超出限制: {error_msg}")
            pytest.fail(f"高德 API 返回错误 (code={error_code}): {error_msg}")
        result = response.get("result", {})
        content = result.get("content", [])
        assert len(content) >= 1, (
            f"amap_hotel_anchor 返回了空的 content。\n"
            f"完整响应: {json.dumps(response, ensure_ascii=False, indent=2)}"
        )
        hotel = json.loads(content[0]["text"])
        assert hotel["id"] == "hotel_anchor"
        assert hotel["category"] == "hotel"
        assert "coordinates" in hotel
        assert "name" in hotel


class TestWeatherConstraints:
    """tools/call: amap_weather_constraints."""

    def test_weather_constraints(self) -> None:
        time.sleep(AMAP_QPS_DELAY)
        payload = _jsonrpc(
            "tools/call",
            {"name": "amap_weather_constraints", "arguments": {"city": "长沙"}},
            request_id=50,
        )
        result = _assert_ok(_post(payload))
        content = result.get("content", [])
        assert len(content) == 1
        constraints = json.loads(content[0]["text"])
        assert isinstance(constraints, list)
        # Weather constraints may be empty if weather is fine
        for c in constraints:
            assert "time_window" in c
            assert "rule" in c
            assert "blocked_categories" in c
            assert "block_outdoor" in c
            assert "reason" in c


class TestLLMComplete:
    """tools/call: llm_complete_text."""

    def test_llm_complete_text(self) -> None:
        """Call the LLM with a simple prompt."""
        payload = _jsonrpc(
            "tools/call",
            {
                "name": "llm_complete_text",
                "arguments": {
                    "system_prompt": "You are a helpful assistant.",
                    "user_prompt": "Say 'Hello' in one word.",
                    "temperature": 0.0,
                },
            },
            request_id=60,
        )
        try:
            response = _post(payload)
        except Exception as exc:
            pytest.skip(f"LLM 服务不可用或超时: {exc}。请检查 LLM 配置（API Key、网络连接等）")
        if "error" in response:
            error_msg = response["error"].get("message", "")
            pytest.fail(
                f"LLM 调用返回错误 (code={response['error']['code']}): {error_msg}\n"
                f"请检查 LLM 配置是否正确"
            )
        result = response.get("result", {})
        content = result.get("content", [])
        assert len(content) >= 1, (
            f"llm_complete_text 返回了空 content。\n"
            f"完整响应: {json.dumps(response, ensure_ascii=False, indent=2)}"
        )
        text = json.loads(content[0]["text"])
        assert "text" in text
        assert isinstance(text["text"], str)
        assert len(text["text"]) > 0

    def test_llm_no_system_prompt(self) -> None:
        """system_prompt is required by the tool schema — this test verifies
        the error response when it is omitted."""
        payload = _jsonrpc(
            "tools/call",
            {
                "name": "llm_complete_text",
                "arguments": {
                    "user_prompt": "Say 'Hi' in one word.",
                },
            },
            request_id=61,
        )
        try:
            response = _post(payload)
        except Exception as exc:
            pytest.skip(f"LLM 服务不可用或超时: {exc}。请检查 LLM 配置")
        if "error" in response:
            # system_prompt is required — 预期行为
            assert response["error"]["code"] == -32602
            assert "system_prompt" in response["error"].get("message", "")
            return
        pytest.fail("预期 tools/call 返回错误（缺少 system_prompt），但实际返回了成功结果")


class TestDistanceMatrix:
    """tools/call: amap_distance_matrix."""

    def test_distance_matrix_simple(self) -> None:
        """Build a distance matrix with 2 POI nodes."""
        time.sleep(AMAP_QPS_DELAY)
        nodes = [
            {
                "id": "poi_A",
                "name": "橘子洲",
                "category": "landmark",
                "coordinates": {"lat": 28.196, "lng": 112.963},
                "fixed_cost": 50.0,
                "visit_duration_minutes": 90,
                "utility": 9.0,
                "opening_window": ["09:00", "18:00"],
                "indoor": False,
            },
            {
                "id": "poi_B",
                "name": "五一广场",
                "category": "shopping",
                "coordinates": {"lat": 28.197, "lng": 112.977},
                "fixed_cost": 0.0,
                "visit_duration_minutes": 60,
                "utility": 7.0,
                "opening_window": ["09:00", "22:00"],
                "indoor": True,
            },
        ]
        payload = _jsonrpc(
            "tools/call",
            {
                "name": "amap_distance_matrix",
                "arguments": {"nodes": nodes, "financial": {}},
            },
            request_id=70,
        )
        response = _post(payload)
        if "error" in response:
            # Amap may be unavailable — fallback matrix is used in that case
            assert response["error"]["code"] in (-32000, -32602)
        else:
            result = response.get("result", {})
            content = result.get("content", [])
            assert len(content) == 1
            matrix = json.loads(content[0]["text"])
            assert isinstance(matrix, dict)
            assert len(matrix) >= 2  # at least 2 directed edges


class TestErrorHandling:
    """JSON-RPC error handling."""

    def test_unknown_tool(self) -> None:
        """Call a tool that does not exist."""
        payload = _jsonrpc(
            "tools/call",
            {"name": "nonexistent_tool", "arguments": {}},
            request_id=80,
        )
        error = _assert_error(_post(payload), -32602)
        assert "nonexistent_tool" in error["message"]

    def test_unknown_method(self) -> None:
        """Call an unsupported JSON-RPC method."""
        payload = _jsonrpc("some_random_method", request_id=81)
        error = _assert_error(_post(payload), -32601)
        assert "some_random_method" in error["message"]

    def test_invalid_json(self) -> None:
        """Send malformed JSON (not a JSON-RPC request)."""
        data = b"this is not json"
        req = urllib.request.Request(
            MCP_ENDPOINT,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8")
                response = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            response = json.loads(raw)
        # The stdlib HTTP server catches JSON decode errors and returns -32700
        assert "error" in response
        assert response["error"]["code"] == -32000  # caught by the generic handler in do_POST

    def test_missing_arguments(self) -> None:
        """Call a tool with missing required arguments."""
        payload = _jsonrpc(
            "tools/call",
            {"name": "amap_poi_search", "arguments": {}},
            request_id=82,
        )
        response = _post(payload)
        # Should error with KeyError for missing 'city' -> -32602
        assert "error" in response
        assert response["error"]["code"] in (-32602, -32000)


class TestStdioServer:
    """Directly test the JSON-RPC message handler (without HTTP)."""

    def test_handle_message_initialize(self) -> None:
        from app.mcp_server.server import handle_message
        response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert response is not None
        assert response["result"]["serverInfo"]["name"] == "trip-planner-mcp"

    def test_handle_message_tools_list(self) -> None:
        from app.mcp_server.server import handle_message
        response = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert response is not None
        tools = response["result"]["tools"]
        assert len(tools) >= 6

    def test_handle_message_unknown_tool(self) -> None:
        from app.mcp_server.server import handle_message
        response = handle_message({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "bad_tool", "arguments": {}},
        })
        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32602