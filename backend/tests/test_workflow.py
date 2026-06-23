from pathlib import Path
from uuid import uuid4

import pytest

from app.agents.intent_agent import parse_trip_intent
from app.algorithms.matrix_builder import build_fallback_matrix, matrix_key
from app.algorithms.geometry import simplify_geometry
from app.algorithms.geo import haversine_km
from app.core.config import BACKEND_ROOT, resolve_backend_path
from app.graph.state import Coordinates, ExportRequest, FinancialContext, IntentConstraints, POICandidate, ReplanRequest
from app.graph import nodes as workflow_nodes
from app.services import provider_adapters
from app.services.amap_service import AmapUnavailableError, _poi_from_amap
from app.graph.workflow import run_replan_workflow, run_trip_workflow
from app.services.export_service import persist_export_payload, render_export_payload
from app.services.job_service import JobStore

TEST_OUTPUT_DIR = BACKEND_ROOT / "test-output"


def _writable_test_dir(*parts: str) -> Path:
    """Return a project-local writable test directory, or skip if unavailable."""
    directory = TEST_OUTPUT_DIR.joinpath(*parts)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        pytest.skip(f"filesystem persistence tests need a writable directory: {exc}")
    return directory


def test_haversine_returns_reasonable_distance():
    origin = Coordinates(lat=31.2304, lng=121.4737)
    destination = Coordinates(lat=31.2397, lng=121.4998)

    assert 2.0 < haversine_km(origin, destination) < 4.0


def test_backend_paths_resolve_from_repo_or_backend_cwd():
    assert resolve_backend_path("data/jobs.jsonl") == BACKEND_ROOT / "data" / "jobs.jsonl"
    assert resolve_backend_path("backend/data/jobs.jsonl") == BACKEND_ROOT / "data" / "jobs.jsonl"


def test_fallback_matrix_contains_time_dependent_hour_slices():
    hotel = POICandidate(
        id="hotel",
        name="Hotel",
        category="hotel",
        coordinates=Coordinates(lat=31.2328, lng=121.4752),
        visit_duration_minutes=0,
    )
    museum = POICandidate(
        id="museum",
        name="Museum",
        category="museum",
        coordinates=Coordinates(lat=31.2304, lng=121.4737),
    )

    matrix = build_fallback_matrix([hotel, museum], FinancialContext())
    off_peak = matrix[matrix_key("hotel", "museum", 11)]
    peak = matrix[matrix_key("hotel", "museum", 18)]

    assert len(matrix) == 48
    assert peak.hour == 18
    assert peak.duration_minutes >= off_peak.duration_minutes


def test_rule_intent_parser_extracts_chinese_trip_constraints():
    intent = parse_trip_intent("上海两天，预算600元，想看博物馆和美食，9:00-18:00")

    assert intent.destination == "Shanghai"
    assert intent.days == 2
    assert intent.budget_limit == 600
    assert intent.time_window_baseline == ("09:00", "18:00")
    assert "museum" in intent.preferences
    assert "food" in intent.preferences


def test_rule_intent_parser_extracts_english_trip_constraints():
    intent = parse_trip_intent("Plan 3 days in Tokyo under 1200 CNY with gardens and shopping")

    assert intent.destination == "Tokyo"
    assert intent.days == 3
    assert intent.budget_limit == 1200
    assert "garden" in intent.preferences
    assert "shopping" in intent.preferences


def test_amap_poi_payload_maps_to_candidate():
    poi = _poi_from_amap(
        {
            "id": "B001",
            "name": "Shanghai Museum",
            "type": "博物馆",
            "location": "121.4737,31.2304",
        },
        fallback_category="museum",
    )

    assert poi is not None
    assert poi.id == "amap_B001"
    assert poi.category == "museum"
    assert poi.coordinates.lat == 31.2304


def test_amap_provider_falls_back_to_local_when_unavailable(monkeypatch: pytest.MonkeyPatch):
    def fail_search(*args, **kwargs):
        raise AmapUnavailableError("offline")

    monkeypatch.setattr(provider_adapters, "search_pois", fail_search)
    provider = provider_adapters.AmapAttractionProvider()

    pois = provider.search(
        IntentConstraints(user_query="demo", destination="Shanghai", preferences=["museum"])
    )

    assert pois
    assert pois[0].id.startswith("poi_")


def test_workflow_returns_rendered_solution():
    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a two-day route.",
            destination="Shanghai",
            days=2,
            budget_limit=700,
        )
    )

    assert state.graph_controls.current_status == "rendered"
    assert len(state.routing_solution.optimized_route) == 2
    assert state.routing_solution.budget_breakdown.total_cost > 0
    assert "Day 1" in state.routing_solution.narrative
    assert state.routing_solution.optimized_route[0].geometry
    assert state.routing_solution.optimized_route[0].bounds is not None
    assert state.routing_solution.quality_metrics.total_stops > 0
    assert state.routing_solution.fitness_curve
    assert any(event["event"] == "provider_status" for event in state.graph_controls.events)
    matrix_events = [event for event in state.graph_controls.events if event["event"] == "matrix_ready"]
    assert matrix_events
    assert matrix_events[-1]["payload"]["source"] in {"fallback", "amap", "cache:fallback", "cache:amap"}


def test_replan_inserts_new_poi_into_target_day():
    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a two-day route.",
            destination="Shanghai",
            days=2,
            budget_limit=900,
        )
    )
    new_poi = POICandidate(
        id="poi_library",
        name="City Library",
        category="library",
        coordinates=Coordinates(lat=31.226, lng=121.471),
        fixed_cost=0,
        visit_duration_minutes=60,
        utility=6.4,
        indoor=True,
    )

    replanned = run_replan_workflow(ReplanRequest(state=state, day=1, new_poi=new_poi))

    day_one_names = [stop.poi.name for stop in replanned.routing_solution.optimized_route[0].stops]
    assert "City Library" in day_one_names
    assert replanned.graph_controls.current_status == "rendered"


def test_replan_rejects_duplicate_poi_on_target_day_when_fastapi_is_available():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import create_app

    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a one-day route.",
            destination="Shanghai",
            days=1,
            budget_limit=900,
        )
    )
    existing_poi = state.routing_solution.optimized_route[0].stops[0].poi

    client = TestClient(create_app())
    response = client.post(
        "/api/trips/replan",
        json={
            "state": state.model_dump(mode="json"),
            "day": 1,
            "new_poi": existing_poi.model_dump(mode="json"),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "trip_planner_error"


def test_replan_budget_repair_keeps_inserted_route_under_limit():
    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a tight one-day route.",
            destination="Shanghai",
            days=1,
            budget_limit=300,
        )
    )
    new_poi = POICandidate(
        id="poi_premium_gallery",
        name="Premium Gallery",
        category="museum",
        coordinates=Coordinates(lat=31.224, lng=121.482),
        fixed_cost=180,
        visit_duration_minutes=60,
        utility=3.5,
        indoor=True,
    )

    replanned = run_replan_workflow(ReplanRequest(state=state, day=1, new_poi=new_poi))

    assert replanned.routing_solution.budget_breakdown.total_cost <= 300
    assert replanned.routing_solution.repair_actions


def test_export_payload_uses_requested_format():
    state = run_trip_workflow(
        IntentConstraints(user_query="demo", destination="Shanghai", days=1, budget_limit=800)
    )
    request = ExportRequest(solution=state.routing_solution, export_format="html")

    payload = render_export_payload(request.solution, request.export_format)

    assert payload["format"] == "html"
    assert "Day 1" in payload["content"]


def test_budget_repair_prunes_when_budget_is_tight():
    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a low budget route.",
            destination="Shanghai",
            days=2,
            budget_limit=260,
        )
    )

    assert state.routing_solution.repair_actions
    assert state.routing_solution.budget_breakdown.total_cost <= 260


def test_budget_repair_can_prune_more_than_three_candidates(monkeypatch: pytest.MonkeyPatch):
    class ExpensiveAttractions:
        def search(self, intent: IntentConstraints) -> list[POICandidate]:
            return [
                POICandidate(
                    id=f"expensive_{index}",
                    name=f"Expensive POI {index}",
                    category="museum",
                    coordinates=Coordinates(lat=31.23 + index * 0.001, lng=121.47 + index * 0.001),
                    fixed_cost=120,
                    visit_duration_minutes=60,
                    utility=5,
                    indoor=True,
                )
                for index in range(6)
            ]

    registry = provider_adapters.ProviderRegistry(attractions=ExpensiveAttractions())
    monkeypatch.setattr(workflow_nodes, "provider_registry", registry)

    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a low budget route.",
            destination="Shanghai",
            days=2,
            budget_limit=260,
        )
    )

    assert len(state.routing_solution.repair_actions) > 3
    assert state.routing_solution.budget_breakdown.total_cost <= 260


def test_job_store_keeps_completed_state_and_events():
    store_path = _writable_test_dir("jobs") / f"{uuid4().hex}.jsonl"
    store = JobStore(storage_path=store_path)
    job = store.submit(
        IntentConstraints(user_query="demo", destination="Shanghai", days=1, budget_limit=800)
    )
    restored = JobStore(storage_path=store_path)

    assert job.status == "complete"
    assert job.state is not None
    assert any(event["event"] == "stage_complete" for event in job.events)
    assert restored.get(job.id) == job
    store_path.unlink(missing_ok=True)


def test_job_store_returns_incremental_events():
    store_path = _writable_test_dir("jobs") / f"{uuid4().hex}.jsonl"
    store = JobStore(storage_path=store_path)
    job = store.submit(
        IntentConstraints(user_query="demo", destination="Shanghai", days=1, budget_limit=800)
    )

    first_window = store.events_since(job.id, 0)
    second_window = store.events_since(job.id, 2)

    assert first_window is not None
    assert second_window is not None
    assert first_window.next_offset == len(job.events)
    assert second_window.events == job.events[2:]
    assert first_window.state is not None
    store_path.unlink(missing_ok=True)


def test_job_events_endpoint_when_fastapi_is_available():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    job_response = client.post(
        "/api/trips/jobs",
        json={
            "user_query": "demo",
            "destination": "Shanghai",
            "days": 1,
            "budget_limit": 800,
            "preferences": [],
        },
    )
    job_id = job_response.json()["id"]

    response = client.get(f"/api/trips/jobs/{job_id}/events?after=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == job_id
    assert payload["next_offset"] >= 1
    assert payload["status"] == "complete"


def test_export_payload_can_be_persisted():
    state = run_trip_workflow(
        IntentConstraints(user_query="demo", destination="Shanghai", days=1, budget_limit=800)
    )
    output_dir = _writable_test_dir("exports", uuid4().hex)

    payload = persist_export_payload(state.routing_solution, output_dir=output_dir)
    output_path = Path(payload["file_path"])

    assert payload["file_path"].endswith(".html")
    assert "Trip Plan" in output_path.read_text(encoding="utf-8")
    output_path.unlink(missing_ok=True)


def test_douglas_peucker_simplifies_route_geometry():
    points = [
        Coordinates(lat=0, lng=0),
        Coordinates(lat=0.00001, lng=0.5),
        Coordinates(lat=0, lng=1),
        Coordinates(lat=0, lng=1.5),
    ]

    simplified = simplify_geometry(points, tolerance=0.001)

    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]
    assert len(simplified) < len(points)


def test_health_endpoint_when_fastapi_is_available():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_capabilities_endpoint_when_fastapi_is_available():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/health/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert "provider_mode" in payload
    assert "amap_enabled" in payload
    assert "llm_enabled" in payload
    assert "api_key" not in payload


def test_integration_probe_endpoint_when_fastapi_is_available():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/health/integrations/probe")

    assert response.status_code == 200
    payload = response.json()
    assert {result["name"] for result in payload["results"]} == {"amap", "llm"}
    assert all(result["status"] in {"ok", "error", "skipped"} for result in payload["results"])


def test_intent_parse_endpoint_when_fastapi_is_available():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.post("/api/trips/intent/parse", json={"user_query": "上海两天预算600元"})

    assert response.status_code == 200
    assert response.json()["destination"] == "Shanghai"
    assert response.json()["days"] == 2
