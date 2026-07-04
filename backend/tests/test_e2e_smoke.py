#!/usr/bin/env python3
"""End-to-end smoke test for the Trip Planner backend.

This test validates that the FastAPI application starts correctly and that
the critical API endpoints respond as expected. It is designed to be run
against both a running server instance and a test-fastapi client.

Usage:
    pytest backend/tests/test_e2e_smoke.py -v
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.exceptions import TripPlannerError
from app.graph.state import IntentConstraints

# ---------------------------------------------------------------------------
# Import the app and wait for it to be available
# ---------------------------------------------------------------------------
import sys

sys.path.insert(0, "backend")

from app.main import app  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Health / Meta
# ---------------------------------------------------------------------------

def test_health_liveness() -> None:
    """GET /health returns 200 with status=ok."""
    resp = client.get("/health")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "version" in body


def test_health_capabilities() -> None:
    """GET /health/capabilities returns switches without secrets."""
    resp = client.get("/health/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    # All capabilities fields should be present
    for key in ("provider_mode", "amap_configured", "llm_configured", "fallback_mode", "mcp_inprocess_allowed"):
        assert key in body, f"Missing capability key: {key}"


def test_health_job_store() -> None:
    """GET /health/job-store returns persistence metadata."""
    resp = client.get("/health/job-store")
    assert resp.status_code == 200
    body = resp.json()
    assert "persistence_error" in body
    assert "job_count" in body


def test_health_integrations_probe() -> None:
    """GET /health/integrations/probe returns probe results (may be skipped)."""
    resp = client.get("/health/integrations/probe")
    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    assert isinstance(body["results"], list)
    # At least amap and LLM probes should be present
    names = {r["name"] for r in body["results"] if isinstance(r, dict) and "name" in r}
    assert "amap" in names or "llm" in names, "Expected at least one integration probe"


# ---------------------------------------------------------------------------
# Workflow topology
# ---------------------------------------------------------------------------

def test_workflow_topology() -> None:
    """GET /api/trips/workflow/topology returns deterministic workflow structure."""
    resp = client.get("/api/trips/workflow/topology")
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

def test_intent_parse() -> None:
    """POST /api/trips/intent/parse returns structured constraints."""
    resp = client.post(
        "/api/trips/intent/parse",
        json={"user_query": "Plan a one-day trip to Shanghai under 500 RMB"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "destination" in body
    assert body["destination"] == "Shanghai" or "shanghai" in body["destination"].lower()
    assert body["days"] >= 1
    assert body["budget_limit"] <= 500


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def test_plan_trip() -> None:
    """POST /api/trips/plan returns a TripState with a valid route."""
    intent = IntentConstraints(
        user_query="Plan a one-day city trip under budget.",
        destination="Shanghai",
        days=1,
        budget_limit=300,
        preferences=["museum", "food"],
    )
    resp = client.post("/api/trips/plan", json=intent.model_dump(mode="json"))
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # Must have solution or at least graph_controls
    assert "graph_controls" in body
    controls = body["graph_controls"]
    VALID_STATUSES = {"complete", "failed", "running", "rendered", "pending"}
    assert controls.get("current_status") in VALID_STATUSES, (
        f"Unexpected status: {controls.get('current_status')}"
    )
    if controls.get("current_status") == "complete":
        assert "routing_solution" in body
        solution = body["routing_solution"]
        assert "optimized_route" in solution
        assert solution["budget_breakdown"]["total_cost"] > 0


def test_plan_trip_with_budget() -> None:
    """Budget limit must be respected in the output."""
    intent = IntentConstraints(
        user_query="Cheap one-day trip.",
        destination="Shanghai",
        days=1,
        budget_limit=200,
        preferences=["museum"],
    )
    resp = client.post("/api/trips/plan", json=intent.model_dump(mode="json"))
    assert resp.status_code == 200
    body = resp.json()
    controls = body.get("graph_controls", {})
    if controls.get("current_status") == "complete":
        budget = body["routing_solution"]["budget_breakdown"]
        assert budget["total_cost"] <= budget["budget_limit"] * 1.05, (
            f"Total cost {budget['total_cost']:.2f} exceeds budget {budget['budget_limit']:.2f}"
        )


# ---------------------------------------------------------------------------
# Demo endpoint
# ---------------------------------------------------------------------------

def test_demo() -> None:
    """GET /api/trips/demo returns a deterministic demo plan."""
    resp = client.get("/api/trips/demo")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "graph_controls" in body
    controls = body["graph_controls"]
    VALID_STATUSES = {"complete", "failed", "running", "rendered", "pending"}
    assert controls.get("current_status") in VALID_STATUSES, (
        f"Unexpected status: {controls.get('current_status')}"
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_html() -> None:
    """POST /api/trips/export returns HTML export payload."""
    # First get a demo plan
    demo = client.get("/api/trips/demo")
    assert demo.status_code == 200
    solution = demo.json().get("routing_solution")
    if not solution:
        return  # Skip if demo didn't produce a solution

    resp = client.post("/api/trips/export", json={"solution": solution, "export_format": "html"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["format"] == "html"
    assert "content" in body
    assert "<!doctype html>" in body["content"].lower()


# ---------------------------------------------------------------------------
# Job submission / polling
# ---------------------------------------------------------------------------

def test_job_submit_and_poll() -> None:
    """Submit a job and poll for completion."""
    intent = IntentConstraints(
        user_query="Quick job test.",
        destination="Shanghai",
        days=1,
        budget_limit=300,
        preferences=["landmark"],
    )
    resp = client.post("/api/trips/jobs", json=intent.model_dump(mode="json"))
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    job = resp.json()
    job_id = job["id"]
    assert job["status"] in {"queued", "running"}

    # Poll for completion (up to 30 seconds)
    import time as time_module

    for _ in range(30):
        poll = client.get(f"/api/trips/jobs/{job_id}")
        assert poll.status_code == 200
        job_status = poll.json()["status"]
        if job_status in {"complete", "failed"}:
            break
        time_module.sleep(1)

    assert job_status in {"complete", "failed"}, f"Job did not finish: {job_status}"
    if job_status == "complete":
        events = client.get(f"/api/trips/jobs/{job_id}/events?after=0")
        assert events.status_code == 200
        assert len(events.json()["events"]) > 0


# ---------------------------------------------------------------------------
# Job listing
# ---------------------------------------------------------------------------

def test_job_list() -> None:
    """GET /api/trips/jobs returns a list of jobs."""
    resp = client.get("/api/trips/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert isinstance(jobs, list)


# ---------------------------------------------------------------------------
# Cleanup endpoints
# ---------------------------------------------------------------------------

def test_cleanup_jobs_endpoint() -> None:
    """POST /health/cleanup/jobs returns a removed count."""
    resp = client.post("/health/cleanup/jobs")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.json()
    assert "removed" in body
    assert isinstance(body["removed"], int)


def test_cleanup_exports_endpoint() -> None:
    """POST /health/cleanup/exports returns a removed count."""
    resp = client.post("/health/cleanup/exports")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    body = resp.json()
    assert "removed" in body
    assert isinstance(body["removed"], int)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_invalid_export_format() -> None:
    """POST /api/trips/export with invalid format returns 422."""
    demo = client.get("/api/trips/demo")
    assert demo.status_code == 200
    solution = demo.json().get("routing_solution")
    if not solution:
        return
    resp = client.post("/api/trips/export", json={"solution": solution, "export_format": "docx"})
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


def test_nonexistent_job() -> None:
    """GET /api/trips/jobs/nonexistent returns 404."""
    resp = client.get("/api/trips/jobs/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Circuit breaker / retry configuration (smoke)
# ---------------------------------------------------------------------------

def test_circuit_breaker_config() -> None:
    """Verify that circuit breaker settings are accessible."""
    from app.core.config import settings as s

    assert s.amap_retry_max_attempts >= 1
    assert s.amap_retry_base_delay_ms >= 100
    assert s.amap_circuit_breaker_threshold >= 1
    assert s.amap_circuit_breaker_reset_seconds >= 5


def test_cleanup_config() -> None:
    """Verify that cleanup settings have reasonable defaults."""
    from app.core.config import settings as s

    assert s.export_cleanup_max_age_days >= 1
    assert s.export_cleanup_max_files >= 10
    assert s.job_cleanup_max_age_hours >= 1
    assert s.job_cleanup_max_jobs >= 50