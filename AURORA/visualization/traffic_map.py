import json
import logging
import os
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


def _build_agent_paths(agents: Dict[str, Any], tick_data: List[Dict]) -> Dict[str, List[Tuple[float, float]]]:
    paths: Dict[str, List[Tuple[float, float]]] = {}
    # Build paths from journey step path_coords (road-following) when available
    for aid, rec in agents.items():
        journey = rec.get("journey") or {}
        steps = journey.get("steps", [])
        coords = []
        for s in steps:
            pc = s.get("path_coords", [])
            if pc:
                coords.extend(pc)
        if coords:
            paths[aid] = coords
    # Fallback: use tick positions for agents without journey data
    for tick in tick_data:
        agents_snapshot = tick.get("agents", {})
        for aid, state in agents_snapshot.items():
            if aid in paths:
                continue
            pos = state.get("position")
            if pos and len(pos) == 2 and pos[0] is not None and pos[1] is not None:
                paths.setdefault(aid, []).append((pos[0], pos[1]))
    return paths


def generate_traffic_map(
    recording_path: str,
    output_path: str = "output/traffic_map.html",
    jam_density_per_km: float = 50.0,
    agent_trails: bool = True,
    center_lat: float = 41.8781,
    center_lon: float = -87.6298,
    zoom: int = 12,
):
    try:
        import folium
        from folium import plugins
    except ImportError:
        logger.error("folium is required. Install with: pip install folium")
        return None

    with open(recording_path) as f:
        sim_data = json.load(f)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom,
                   tiles="cartodbpositron", control_scale=True)
    fullscreen = plugins.Fullscreen()
    m.add_child(fullscreen)

    mgr = plugins.MeasureControl(position="bottomleft")
    m.add_child(mgr)

    agents = sim_data.get("agents", {})
    ticks = sim_data.get("ticks", [])
    summary = sim_data.get("summary", {})

    info_html = f"""
    <div style="position: fixed; top: 10px; right: 10px; z-index: 1000;
                background: white; padding: 12px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15); font-family: sans-serif;
                font-size: 13px; max-width: 260px;">
        <b>AURORA Simulation</b><br>
        Agents: {summary.get('n_agents', 0)} &nbsp; Arrived: {summary.get('n_arrived', 0)}<br>
        Ticks: {summary.get('n_ticks', 0)}<br>
        Density: {jam_density_per_km:.0f} agents/km
    </div>
    """
    m.get_root().html.add_child(folium.Element(info_html))

    if agent_trails:
        paths = _build_agent_paths(agents, ticks)
        agent_colors = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        ]
        color_idx = 0

        # Collect all waypoint positions for heatmap
        heat_data = []
        for aid in sorted(paths.keys()):
            pts = paths[aid]
            if len(pts) < 2:
                continue
            color = agent_colors[color_idx % len(agent_colors)]
            folium.PolyLine(
                locations=pts,
                color=color,
                weight=2,
                opacity=0.7,
                popup=f"Agent: {aid} ({len(pts)} positions)",
                tooltip=aid,
            ).add_to(m)
            folium.CircleMarker(
                location=pts[0],
                radius=5,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=f"{aid} start",
            ).add_to(m)
            if len(pts) > 1:
                folium.CircleMarker(
                    location=pts[-1],
                    radius=5,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.8,
                    popup=f"{aid} end",
                    marker=folium.Icon(icon="flag", prefix="fa"),
                ).add_to(m)
            color_idx += 1
            heat_data.extend(pts)

        # Traffic heatmap based on agent positions
        if heat_data:
            from folium.plugins import HeatMap
            HeatMap(heat_data, radius=15, blur=10, min_opacity=0.3).add_to(m)
            logger.info(f"Rendered heatmap from {len(heat_data)} agent positions")

        logger.info(f"Rendered {len(paths)} agent trails on map")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    m.save(output_path)
    logger.info(f"Traffic map saved to {output_path}")
    return output_path
