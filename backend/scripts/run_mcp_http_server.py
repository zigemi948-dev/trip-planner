from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.mcp_server.server import handle_message


class MCPRequestHandler(BaseHTTPRequestHandler):
    server_version = "TripPlannerMCP/0.1"

    def do_GET(self) -> None:
        if self.path == "/":
            self._send_json(
                {
                    "name": "Trip Planner MCP HTTP server",
                    "status": "ok",
                    "endpoints": {
                        "health": "GET /health",
                        "json_rpc": "POST /mcp",
                    },
                    "example": {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                        "params": {},
                    },
                }
            )
            return
        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json({"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.send_error(404)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(content_length).decode("utf-8")
            message = json.loads(payload)
            response = handle_message(message) or {"jsonrpc": "2.0", "result": None}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": str(exc)},
            }
        self._send_json(response)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(format % args + "\n")

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), MCPRequestHandler)
    print("Trip Planner MCP HTTP server listening on http://127.0.0.1:8765", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
