from pathlib import Path
from uuid import uuid4
from html import escape
import base64
import io

# [新增说明]: 引入外部请求与可视化渲染库
import requests
import matplotlib.pyplot as plt
from weasyprint import HTML as WeasyHTML

from app.core.config import resolve_backend_path,settings
from app.graph.state import RoutingSolution


def render_export_payload(solution: RoutingSolution, export_format: str = "html") -> dict[str, str]:
    """Prepare a minimal HTML payload for future headless PDF rendering."""
    # HTML作为中间态渲染引擎，注入图表和外部图像
    html = _solution_to_html(solution)
    return {
        "format": export_format,
        "content_type": {
            "html": "text/html",
            "pdf": "application/pdf",
            "png": "image/png"
        }.get(export_format, "text/plain"),
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

    suffix = export_format
    output_path = directory / f"trip-{uuid4().hex}.{suffix}"

    # [修改说明]: 增加对 PDF 和 PNG 的物理文件写入支持
    if export_format == "html":
        output_path.write_text(payload["content"], encoding="utf-8")
    elif export_format == "pdf":
        WeasyHTML(string=payload["content"]).write_pdf(output_path)
    elif export_format == "png":
        # PNG渲染通常需要无头浏览器引擎，这里使用WeasyPrint生成PDF后光栅化或使用外部工具
        # 为保持轻量，这里通过WeasyPrint生成单页/长图模式的PNG (需安装依赖)
        doc = WeasyHTML(string=payload["content"]).render()
        # 提取第一页作为PNG示例（实际复杂长图建议集成 Playwright）
        doc.pages[0].write_png(output_path)
    else:
        output_path.write_text(payload["content"], encoding="utf-8")

    payload["file_path"] = str(output_path.resolve())
    return payload


# -------------------------------------------------------------------------
# [新增私有算法模块]: 数据处理与外部I/O隔离层
# -------------------------------------------------------------------------

def _generate_budget_chart_base64(budget) -> str:
    """生成预算分配饼图并进行光栅化 (Base64编码)"""
    labels = ['Tickets', 'Transport', 'Hotel', 'Remaining']
    sizes = [budget.fixed_cost, budget.transport_cost, budget.accommodation_cost, budget.remaining]
    # 过滤掉为0的项以防止图表重叠
    filtered_data = [(l, s) for l, s in zip(labels, sizes) if s > 0]
    if not filtered_data:
        return ""
    
    fl, fs = zip(*filtered_data)
    
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.pie(fs, labels=fl, autopct='%1.1f%%', startangle=90, colors=['#3498db', '#e74c3c', '#2ecc71', '#f1c40f'])
    ax.axis('equal')  # 保证饼图为正圆形
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', transparent=True)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def _fetch_unsplash_image(query: str) -> str:
    """调用Unsplash API获取目的地的视觉表征"""
    if not settings.unsplash_access_key or settings.unsplash_access_key.startswith("YOUR"):
        return "" # 缺乏密钥时降级处理
    try:
        url = f"https://api.unsplash.com/photos/random?query={query}&orientation=landscape"
        headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code == 200:
            return response.json().get("urls", {}).get("regular", "")
    except Exception as e:
        # 实际工程中应接入日志系统
        pass
    return ""

def _generate_static_map_url(solution: RoutingSolution) -> str:
    """计算路径最小外包矩形并获取高德静态地图 (GIS算法逻辑)"""
    if not settings.amap_api_key or settings.amap_api_key.startswith("YOUR"):
        return ""
    
    # 提取所有路径点的坐标 (假设 stop.poi 中含有 location 属性，格式为 "lng,lat")
    markers = []
    for route in solution.optimized_route:
        for stop in route.stops:
            if hasattr(stop.poi, 'location') and stop.poi.location:
                markers.append(stop.poi.location)
    
    if not markers:
        return ""
    
    # 构建高德静态地图的 markers 参数 (格式: mid,,A:lng1,lat1;lng2,lat2)
    path_str = ";".join(markers)
    # 自动适应缩放比例
    url = f"https://restapi.amap.com/v3/staticmap?markers=mid,,A:{path_str}&key={settings.amap_api_key}&size=800*400"
    return url


# -------------------------------------------------------------------------
# 原有核心逻辑修改
# -------------------------------------------------------------------------

def _solution_to_html(solution: RoutingSolution) -> str:
    # [新增]: 获取目的地主题图 (取路线中第一个POI所在的城市作为Query)
    theme_image_url = ""
    if solution.optimized_route and solution.optimized_route[0].stops:
        first_poi_name = solution.optimized_route[0].stops[0].poi.name
        theme_image_url = _fetch_unsplash_image(f"{first_poi_name} city architecture")

    # [新增]: 渲染预算图表
    chart_b64 = _generate_budget_chart_base64(solution.budget_breakdown)
    chart_img_tag = f'<img src="data:image/png;base64,{chart_b64}" alt="Budget Chart" style="max-width: 100%; height: auto;" />' if chart_b64 else ""

    # [新增]: 渲染静态地图截图
    map_url = _generate_static_map_url(solution)
    map_img_tag = f'<img src="{map_url}" alt="Route Map" style="width: 100%; border-radius: 8px; margin: 16px 0;" />' if map_url else ""

    # [新增]: 生成每日路线摘要 (归纳算法)
    daily_summaries = []
    for route in solution.optimized_route:
        pois = [stop.poi.name for stop in route.stops]
        path_flow = " ➔ ".join(pois)
        daily_summaries.append(f"<li><strong>Day {route.day}:</strong> {escape(path_flow)}</li>")
    daily_summary_html = "<ul>" + "".join(daily_summaries) + "</ul>" if daily_summaries else "<p>No daily summary available.</p>"


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
    
    # [修改说明]: 将新增的图表、地图、摘要模块和Unsplash图片植入HTML结构
    hero_image = f'<img src="{theme_image_url}" alt="Destination" style="width: 100%; height: 250px; object-fit: cover; border-radius: 8px;" />' if theme_image_url else ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Trip Plan Export</title>
  <style>
    body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 32px; color: #172033; max-width: 900px; margin: 0 auto; padding: 20px; }}
    h1 {{ margin-top: 20px; }}
    h2 {{ border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 30px; }}
    pre {{ white-space: pre-wrap; background: #f6f8fb; padding: 16px; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
    th, td {{ border-bottom: 1px solid #d9e0ea; padding: 10px; text-align: left; font-size: 14px; }}
    th {{ background-color: #f6f8fb; }}
    .budget {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }}
    .budget div {{ background: #f6f8fb; border-radius: 8px; padding: 12px; text-align: center; border: 1px solid #e1e8f0; }}
    .flex-container {{ display: flex; gap: 20px; align-items: flex-start; margin-top: 20px; }}
    .flex-child {{ flex: 1; }}
  </style>
</head>
<body>
  {hero_image}
  <h1>Trip Plan</h1>
  <pre>{narrative}</pre>
  
  <h2>Route Map & Summary</h2>
  {map_img_tag}
  <div class="summary-box">
      <h3>Daily Flow</h3>
      {daily_summary_html}
  </div>

  <h2>Budget Overview</h2>
  <div class="flex-container">
      <div class="flex-child">
        <section class="budget">
          <div><strong>Total</strong><br />{budget.total_cost:.2f}</div>
          <div><strong>Limit</strong><br />{budget.budget_limit:.2f}</div>
          <div><strong>Tickets</strong><br />{budget.fixed_cost:.2f}</div>
          <div><strong>Transport</strong><br />{budget.transport_cost:.2f}</div>
          <div><strong>Remaining</strong><br />{budget.remaining:.2f}</div>
          <div><strong>Hotel</strong><br />{budget.accommodation_cost:.2f}</div>
        </section>
      </div>
      <div class="flex-child" style="text-align: center;">
          {chart_img_tag}
      </div>
  </div>

  <h2>Daily Cost Details</h2>
  <table>
    <thead>
      <tr><th>Day</th><th>Hotel</th><th>Tickets</th><th>Food</th><th>Transport</th><th>Total</th></tr>
    </thead>
    <tbody>{cost_rows or "<tr><td colspan='6'>No daily costs</td></tr>"}</tbody>
  </table>
  
  <h2>Daily Weather</h2>
  <table>
    <thead>
      <tr><th>Day</th><th>Date</th><th>Weather</th><th>Temp</th><th>Wind</th><th>Advisory</th></tr>
    </thead>
    <tbody>{weather_rows or "<tr><td colspan='6'>No weather forecast</td></tr>"}</tbody>
  </table>
  
  <h2>Hotel Stay</h2>
  <table>
    <thead>
      <tr><th>Day</th><th>Hotel</th><th>Check-in</th><th>Departure</th><th>Note</th></tr>
    </thead>
    <tbody>{hotel_rows or "<tr><td colspan='5'>No hotel assigned</td></tr>"}</tbody>
  </table>
  
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


def _temperature_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:.0f}-{high:.0f} °C"
    if high is not None:
        return f"{high:.0f} °C"
    if low is not None:
        return f"{low:.0f} °C"
    return ""