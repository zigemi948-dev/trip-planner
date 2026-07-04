from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from uuid import uuid4

from app.core.config import resolve_backend_path
from app.graph.state import Coordinates, RoutingSolution


def render_export_payload(
    solution: RoutingSolution, 
    export_format: str = "html",
    map_snapshot_base64: str | None = None
) -> dict[str, str]:
    """Render a deterministic, self-contained export payload.

    The HTML is intentionally local-only: charts and route maps are embedded as
    SVG data URIs so tests and offline demos never depend on external image APIs.
    """
    # 注入快照参数
    html = _solution_to_html(solution, map_snapshot_base64)
    return {
        "format": export_format,
        "content_type": {
            "html": "text/html",
            "pdf": "application/pdf",
            "png": "image/png",
        }.get(export_format, "text/plain"),
        "content": html,
    }


def persist_export_payload(
    solution: RoutingSolution,
    export_format: str = "html",
    output_dir: str | Path = "exports",
    map_snapshot_base64: str | None = None
) -> dict[str, str]:
    """Write an export artifact to disk and return metadata for the UI."""
    # 注入快照参数
    payload = render_export_payload(solution, export_format, map_snapshot_base64)
    directory = resolve_backend_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = export_format
    output_path = directory / f"trip-{uuid4().hex}.{suffix}"
    html = payload["content"]

    if export_format == "html":
        output_path.write_text(html, encoding="utf-8")
    elif export_format == "pdf":
        _write_pdf(html, output_path)
    elif export_format == "png":
        _write_png(html, output_path)
    else:
        output_path.write_text(html, encoding="utf-8")

    payload["file_path"] = str(output_path.resolve())
    return payload


def _write_pdf(html: str, output_path: Path) -> None:
    try:
        from weasyprint import HTML as WeasyHTML

        WeasyHTML(string=html).write_pdf(output_path)
    except Exception:
        output_path.write_bytes(_minimal_pdf_bytes("Trip Plan Export"))


def _write_png(html: str, output_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # 启动无头 Chromium
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # 设置一个适合旅行路线展示的基础视口宽度，高度会由于 full_page=True 自动延展
            page.set_viewport_size({"width": 960, "height": 1080})
            
            # 注入 HTML 。由于图片和图表已转为 data URI，此处渲染无需额外网络请求
            page.set_content(html, wait_until="networkidle")
            
            # 截取全页面长图，确保多天的行程均被保存
            page.screenshot(path=output_path, full_page=True)
            
            browser.close()
            return
    except ImportError:
        # 如果所在环境（如 CI/CD）未安装 playwright，静默回退到占位图，避免阻断核心流程
        pass
    except Exception:
        pass
    
    # 异常或降级情况下的占位图兜底
    output_path.write_bytes(_placeholder_png_bytes())


def _solution_to_html(solution: RoutingSolution, map_snapshot_base64: str | None = None) -> str:
    chart_img_tag = _image_tag(_budget_chart_data_uri(solution), "Budget Chart", "budget-chart")
    # 核心策略：如果存在前端传来的快照，直接复用（自带 data:image 协议头）；否则降级到后端纯计算的矢量 SVG
    map_data_uri = map_snapshot_base64 if map_snapshot_base64 else _route_map_data_uri(solution)
    map_img_tag = _image_tag(map_data_uri, "Route Map", "route-map")
    daily_summary_html = _daily_summary_html(solution)

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
    weather_rows = "".join(
        f"<tr><td>{forecast.day}</td><td>{escape(forecast.date)}</td>"
        f"<td>{escape(forecast.weather)}</td><td>{_temperature_text(forecast.temperature_min, forecast.temperature_max)}</td>"
        f"<td>{escape(forecast.wind)}</td><td>{escape(forecast.advisory)}</td></tr>"
        for forecast in solution.daily_weather
    )
    hotel_rows = "".join(
        f"<tr><td>{stay.day}</td><td>{escape(stay.hotel.name)}</td>"
        f"<td>{escape(stay.check_in_time)}</td><td>{escape(stay.check_out_time)}</td>"
        f"<td>{escape(stay.note)}</td></tr>"
        for stay in solution.hotel_stays
    )
    cost_rows = "".join(
        f"<tr><td>{cost.day}</td><td>{cost.accommodation_cost:.2f}</td><td>{cost.ticket_cost:.2f}</td>"
        f"<td>{cost.food_cost:.2f}</td><td>{cost.transport_cost:.2f}</td><td>{cost.total_cost:.2f}</td></tr>"
        for cost in solution.daily_costs
    )
    narrative = escape(solution.narrative)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Trip Plan Export</title>
  <style>
    body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 0 auto; color: #172033; max-width: 920px; padding: 24px; }}
    h1 {{ margin-top: 8px; }}
    h2 {{ border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 30px; }}
    pre {{ white-space: pre-wrap; background: #f6f8fb; padding: 16px; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
    th, td {{ border-bottom: 1px solid #d9e0ea; padding: 10px; text-align: left; font-size: 14px; }}
    th {{ background-color: #f6f8fb; }}
    .budget {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }}
    .budget div {{ background: #f6f8fb; border-radius: 8px; padding: 12px; text-align: center; border: 1px solid #e1e8f0; }}
    .visual-grid {{ display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(220px, 0.75fr); gap: 18px; align-items: start; }}
    .route-map, .budget-chart {{ width: 100%; max-width: 100%; border-radius: 8px; border: 1px solid #d9e0ea; }}
    .route-map {{ object-fit: cover; aspect-ratio: 16/9; }}
    .summary-box {{ background: #f6f8fb; border-radius: 8px; padding: 12px 16px; }}
  </style>
</head>
<body>
  <h1>Trip Plan</h1>
  <pre>{narrative}</pre>

  <h2>Route Map & Summary</h2>
  <div class="visual-grid">
    <div>
      {map_img_tag}
      <div class="summary-box">
        <h3>Daily Flow</h3>
        {daily_summary_html}
      </div>
    </div>
    <div>{chart_img_tag}</div>
  </div>

  <h2>Budget Overview</h2>
  <section class="budget">
    <div><strong>Total</strong><br />{budget.total_cost:.2f}</div>
    <div><strong>Limit</strong><br />{budget.budget_limit:.2f}</div>
    <div><strong>Tickets</strong><br />{budget.fixed_cost:.2f}</div>
    <div><strong>Transport</strong><br />{budget.transport_cost:.2f}</div>
    <div><strong>Remaining</strong><br />{budget.remaining:.2f}</div>
    <div><strong>Hotel</strong><br />{budget.accommodation_cost:.2f}</div>
  </section>

  <h2>Daily Cost Details</h2>
  <table>
    <thead><tr><th>Day</th><th>Hotel</th><th>Tickets</th><th>Food</th><th>Transport</th><th>Total</th></tr></thead>
    <tbody>{cost_rows or "<tr><td colspan='6'>No daily costs</td></tr>"}</tbody>
  </table>

  <h2>Daily Weather</h2>
  <table>
    <thead><tr><th>Day</th><th>Date</th><th>Weather</th><th>Temp</th><th>Wind</th><th>Advisory</th></tr></thead>
    <tbody>{weather_rows or "<tr><td colspan='6'>No weather forecast</td></tr>"}</tbody>
  </table>

  <h2>Hotel Stay</h2>
  <table>
    <thead><tr><th>Day</th><th>Hotel</th><th>Check-in</th><th>Departure</th><th>Note</th></tr></thead>
    <tbody>{hotel_rows or "<tr><td colspan='5'>No hotel assigned</td></tr>"}</tbody>
  </table>

  <h2>Route Details</h2>
  <table>
    <thead><tr><th>Day</th><th>Arrival</th><th>Departure</th><th>Stop</th><th>Mode</th><th>Distance</th><th>Cost</th></tr></thead>
    <tbody>{route_rows or "<tr><td colspan='7'>No route stops</td></tr>"}</tbody>
  </table>

  <h2>Warnings</h2>
  <ul>{warnings or "<li>None</li>"}</ul>

  <h2>Automatic Repairs</h2>
  <ul>{repairs or "<li>None</li>"}</ul>
</body>
</html>"""


def _daily_summary_html(solution: RoutingSolution) -> str:
    items = []
    for route in solution.optimized_route:
        stops = [escape(stop.poi.name) for stop in route.stops]
        flow = " -> ".join(stops) if stops else "No planned stops"
        items.append(f"<li><strong>Day {route.day}:</strong> {flow}</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else "<p>No daily summary available.</p>"


def _budget_chart_data_uri(solution: RoutingSolution) -> str:
    budget = solution.budget_breakdown
    values = [
        ("Tickets", budget.fixed_cost, "#3498db"),
        ("Transport", budget.transport_cost, "#e74c3c"),
        ("Hotel", budget.accommodation_cost, "#2ecc71"),
        ("Remaining", max(0.0, budget.remaining), "#f1c40f"),
    ]
    total = sum(value for _, value, _ in values)
    if total <= 0:
        return ""

    width = 520
    row_height = 32
    rows = []
    y = 34
    for label, value, color in values:
        ratio = value / total
        bar_width = max(2, round(ratio * 300))
        rows.append(
            f'<text x="24" y="{y + 15}" font-size="14">{escape(label)}</text>'
            f'<rect x="120" y="{y}" width="{bar_width}" height="20" rx="4" fill="{color}" />'
            f'<text x="{132 + bar_width}" y="{y + 15}" font-size="13">{value:.2f}</text>'
        )
        y += row_height
    height = 56 + len(values) * row_height
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#ffffff" />'
        '<text x="24" y="24" font-size="18" font-weight="700">Budget Chart</text>'
        + "".join(rows)
        + "</svg>"
    )
    return _svg_data_uri(svg)


def _route_map_data_uri(solution: RoutingSolution) -> str:
    points = _route_points(solution)
    if not points:
        return _svg_data_uri(_empty_map_svg())

    width = 800
    height = 360
    padding = 32
    min_lat = min(point.lat for point in points)
    max_lat = max(point.lat for point in points)
    min_lng = min(point.lng for point in points)
    max_lng = max(point.lng for point in points)
    lat_span = max(max_lat - min_lat, 0.0001)
    lng_span = max(max_lng - min_lng, 0.0001)

    def project(point: Coordinates) -> tuple[float, float]:
        x = padding + ((point.lng - min_lng) / lng_span) * (width - padding * 2)
        y = height - padding - ((point.lat - min_lat) / lat_span) * (height - padding * 2)
        return x, y

    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in (project(point) for point in points))
    markers = []
    for index, point in enumerate(points[:20], start=1):
        x, y = project(point)
        label = "H" if index == 1 else str(index - 1)
        markers.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="#ffffff" stroke="#1d4ed8" stroke-width="3" />'
            f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="10" font-weight="700">{label}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" rx="12" fill="#eef6ff" />
  <path d="M0 300 C160 240 300 320 440 260 S680 250 800 190" fill="none" stroke="#d7e7f7" stroke-width="46" />
  <polyline points="{polyline}" fill="none" stroke="#1d4ed8" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" />
  {''.join(markers)}
  <text x="24" y="32" font-size="18" font-weight="700" fill="#172033">Route Map Snapshot</text>
</svg>"""
    return _svg_data_uri(svg)


def _route_points(solution: RoutingSolution) -> list[Coordinates]:
    points: list[Coordinates] = []
    if solution.hotel_anchor is not None:
        points.append(solution.hotel_anchor.coordinates)
    for route in solution.optimized_route:
        if route.geometry:
            points.extend(route.geometry)
        else:
            points.extend(stop.poi.coordinates for stop in route.stops)
    return points


def _empty_map_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="360" viewBox="0 0 800 360">'
        '<rect width="100%" height="100%" rx="12" fill="#eef6ff" />'
        '<text x="400" y="180" text-anchor="middle" font-size="20" fill="#172033">No route geometry available</text>'
        "</svg>"
    )


def _image_tag(data_uri: str, alt: str, class_name: str) -> str:
    if not data_uri:
        return ""
    return f'<img src="{data_uri}" alt="{escape(alt)}" class="{class_name}" />'


def _svg_data_uri(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _placeholder_png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )


def _minimal_pdf_bytes(title: str) -> bytes:
    safe_title = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe_title}) Tj ET"
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj\n".encode("ascii"),
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for item in objects:
        offsets.append(len(output))
        output.extend(item)
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _temperature_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:.0f}-{high:.0f} °C"
    if high is not None:
        return f"{high:.0f} °C"
    if low is not None:
        return f"{low:.0f} °C"
    return ""


# ---------------------------------------------------------------------------
# Export cleanup
# ---------------------------------------------------------------------------
import logging as _logging
import time as time_module

_logger = _logging.getLogger(__name__)


def cleanup_old_exports(
    export_dir: str | Path = "exports",
    max_age_days: int | None = None,
    max_files: int | None = None,
) -> int:
    """Remove export artifacts exceeding age or count limits.

    Returns the number of removed files.
    """
    from app.core.config import settings

    age_limit = max_age_days or settings.export_cleanup_max_age_days
    count_limit = max_files or settings.export_cleanup_max_files
    directory = resolve_backend_path(export_dir)

    if not directory.exists():
        return 0

    now = time_module.time()
    cutoff = now - (age_limit * 86400)
    removed = 0

    # Collect all export files sorted by modification time (oldest first)
    export_files = sorted(
        [p for p in directory.iterdir() if p.is_file()],
        key=lambda p: p.stat().st_mtime,
    )

    # 1. Remove by age
    files_to_remove: list[Path] = []
    for path in list(export_files):
        try:
            if path.stat().st_mtime < cutoff:
                files_to_remove.append(path)
                export_files.remove(path)
        except OSError:
            continue

    # 2. If still over count limit, remove oldest
    overflow = len(export_files) - count_limit
    if overflow > 0:
        files_to_remove.extend(export_files[:overflow])

    # 3. Delete
    for path in files_to_remove:
        try:
            path.unlink()
            removed += 1
            _logger.debug("Removed stale export: %s", path)
        except OSError:
            pass

    if removed:
        _logger.info("Export cleanup: removed %d files (age>%dd or count>%d)", removed, age_limit, count_limit)
    return removed
