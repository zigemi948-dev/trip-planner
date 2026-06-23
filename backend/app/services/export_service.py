from pathlib import Path
from uuid import uuid4
from html import escape

from app.core.config import resolve_backend_path
from app.graph.state import RoutingSolution


def render_export_payload(solution: RoutingSolution, export_format: str = "html") -> dict[str, str]:
    """Prepare a minimal HTML payload for future headless PDF rendering."""
    html = _solution_to_html(solution)
    return {
        "format": export_format,
        "content_type": "text/html" if export_format == "html" else f"application/{export_format}",
        "content": html,
    }


def persist_export_payload(
    solution: RoutingSolution,
    export_format: str = "html",
    output_dir: str | Path = "exports",
) -> dict[str, str]:
    """Write an export artifact to disk and return metadata for the UI."""
    payload = render_export_payload(solution, export_format)
    directory = resolve_backend_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = "html" if export_format == "html" else "txt"
    output_path = directory / f"trip-{uuid4().hex}.{suffix}"
    output_path.write_text(payload["content"], encoding="utf-8")
    payload["file_path"] = str(output_path.resolve())
    return payload


def _solution_to_html(solution: RoutingSolution) -> str:
    route_rows = "".join(
        f"<tr><td>{route.day}</td><td>{escape(stop.arrival_time)}</td>"
        f"<td>{escape(stop.departure_time)}</td><td>{escape(stop.poi.name)}</td>"
        f"<td>{escape(stop.inbound_mode.value if stop.inbound_mode else 'Start')}</td>"
        f"<td>{stop.inbound_distance_km:.2f} km</td><td>{stop.inbound_cost:.2f}</td></tr>"
        for route in solution.optimized_route
        for stop in route.stops
    )
    budget = solution.budget_breakdown
    warnings = "".join(f"<li>{escape(warning)}</li>" for warning in solution.warnings)
    repairs = "".join(
        f"<li>{escape(action.removed_poi_name)}: {escape(action.reason)}</li>"
        for action in solution.repair_actions
    )
    narrative = escape(solution.narrative)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Trip Plan Export</title>
  <style>
    body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 32px; color: #172033; }}
    h1 {{ margin-top: 0; }}
    pre {{ white-space: pre-wrap; background: #f6f8fb; padding: 16px; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
    th, td {{ border-bottom: 1px solid #d9e0ea; padding: 8px; text-align: left; }}
    .budget {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
    .budget div {{ background: #f6f8fb; border-radius: 8px; padding: 12px; }}
  </style>
</head>
<body>
  <h1>Trip Plan</h1>
  <pre>{narrative}</pre>
  <h2>Budget</h2>
  <section class="budget">
    <div><strong>Total</strong><br />{budget.total_cost:.2f}</div>
    <div><strong>Limit</strong><br />{budget.budget_limit:.2f}</div>
    <div><strong>Tickets</strong><br />{budget.fixed_cost:.2f}</div>
    <div><strong>Transport</strong><br />{budget.transport_cost:.2f}</div>
    <div><strong>Remaining</strong><br />{budget.remaining:.2f}</div>
  </section>
  <h2>Route Details</h2>
  <table>
    <thead>
      <tr><th>Day</th><th>Arrival</th><th>Departure</th><th>Stop</th><th>Mode</th><th>Distance</th><th>Cost</th></tr>
    </thead>
    <tbody>{route_rows or "<tr><td colspan='7'>No route stops</td></tr>"}</tbody>
  </table>
  <h2>Warnings</h2>
  <ul>{warnings or "<li>None</li>"}</ul>
  <h2>Automatic Repairs</h2>
  <ul>{repairs or "<li>None</li>"}</ul>
</body>
</html>"""
