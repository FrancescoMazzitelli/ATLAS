import json
import logging
import os
import math
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def generate_path_map(
    journey: dict,
    output_path: str = "output/path_map.html",
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

    gold_shape = journey.get("gold_shape", [])
    steps = journey.get("steps", [])
    gold_maneuvers = journey.get("gold_maneuvers", [])
    log = journey.get("log", [])
    agent_id = journey.get("agent_id", "unknown")
    status = journey.get("status", "unknown")

    if not gold_shape and not steps:
        logger.warning("No path data to render")
        return None

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom,
                   tiles="cartodbpositron", control_scale=True)
    fullscreen = plugins.Fullscreen()
    m.add_child(fullscreen)
    mgr = plugins.MeasureControl(position="bottomleft")
    m.add_child(mgr)

    # ── gold path (dashed blue) ──
    gold_line = [(pt[0], pt[1]) for pt in gold_shape if pt and len(pt) >= 2]
    if gold_line:
        folium.PolyLine(
            locations=gold_line,
            color="#3388ff",
            weight=3,
            opacity=0.5,
            dash_array="10, 10",
            popup=f"Gold path ({len(gold_line)} pts)",
            tooltip="Gold path",
        ).add_to(m)

    # ── gold path maneuver markers ──
    gs = gold_line
    for i, mv in enumerate(gold_maneuvers):
        begin = mv.get("begin_shape_index", 0)
        if begin < len(gs):
            pt = gs[begin]
            streets = mv.get("street_names", [])
            instr = mv.get("instruction", "")
            folium.CircleMarker(
                location=pt,
                radius=4,
                color="#3388ff",
                fill=True,
                fill_color="#3388ff",
                fill_opacity=0.6,
                popup=f"<b>M{i}:</b> {instr}<br>{', '.join(streets) if streets else ''}",
                tooltip=f"M{i}: {', '.join(streets) if streets else instr[:30]}",
            ).add_to(m)

    # ── agent path (red, road-following) ──
    # Build full agent trajectory from path_coords of each step
    agent_full_path = []
    for s in steps:
        coords = s.get("path_coords", [])
        if coords:
            agent_full_path.extend(coords)
        else:
            frm = s.get("from")
            to = s.get("to")
            if frm and len(frm) >= 2 and frm[0] is not None:
                agent_full_path.append((float(frm[0]), float(frm[1])))
            if to and len(to) >= 2 and to[0] is not None:
                agent_full_path.append((float(to[0]), float(to[1])))

    if len(agent_full_path) >= 2:
        folium.PolyLine(
            locations=agent_full_path,
            color="#e74c3c",
            weight=3,
            opacity=0.9,
            popup=f"Agent path ({len(agent_full_path)} pts)",
            tooltip="Agent path",
        ).add_to(m)

    # ── decision markers on agent path with reasoning ──
    for i, s in enumerate(steps):
        reasoning = s.get("reasoning", "")
        seg = s.get("segment", "")
        frm = s.get("from")
        disruptions = s.get("disruptions_on_route", [])

        if frm and len(frm) >= 2 and frm[0] is not None:
            pt = (float(frm[0]), float(frm[1]))
            d_text = ""
            if disruptions:
                d_text = "<br>🚧 " + "; ".join(
                    f"[{d['severity'].upper()}] {d['type']}" for d in disruptions[:2]
                )

            popup_text = f"<b>Step {i}</b>"
            if seg:
                popup_text += f"<br>Chose: <b>{seg}</b>"
            popup_text += f"<br>Maneuver: {s.get('maneuver', '?')}"
            if reasoning:
                popup_text += f"<br><br><i>{reasoning}</i>"
            popup_text += d_text

            color = "#e74c3c"
            folium.CircleMarker(
                location=pt,
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
                popup=folium.Popup(popup_text, max_width=350),
                tooltip=f"Step {i}: {seg}" + (" ⚠" if disruptions else ""),
            ).add_to(m)

    # ── destination marker ──
    if steps:
        last_to = steps[-1].get("to")
        if last_to and len(last_to) >= 2 and last_to[0] is not None:
            folium.Marker(
                location=(float(last_to[0]), float(last_to[1])),
                popup=f"<b>Destination</b><br>{status}",
                icon=folium.Icon(color="green", icon="flag", prefix="fa"),
            ).add_to(m)

    # ── origin marker ──
    if steps:
        first_from = steps[0].get("from")
        if first_from and len(first_from) >= 2 and first_from[0] is not None:
            folium.Marker(
                location=(float(first_from[0]), float(first_from[1])),
                popup=f"<b>Origin</b><br>{agent_id}",
                icon=folium.Icon(color="blue", icon="play", prefix="fa"),
            ).add_to(m)

    # ── info panel with decision summary ──
    agent_coords = [s.get("from") for s in steps if s.get("from")]
    n_steps = len(steps)
    n_gold_pts = len(gold_line)
    n_gold_mv = len(gold_maneuvers)

    # Build a small decision log
    decision_lines = ""
    for s in steps:
        seg = s.get("segment", "?")
        reasoning = s.get("reasoning", "")[:300]
        disr = "⚠" if s.get("disruptions_on_route") else " "
        decision_lines += f"  {disr} S{s['index']}: {seg} — {reasoning}<br>"

    info_html = f"""
    <div style="position: fixed; top: 10px; right: 10px; z-index: 1000;
                background: white; padding: 12px; border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15); font-family: sans-serif;
                font-size: 12px; max-width: 320px; max-height: 80vh; overflow-y: auto;">
        <b>AURORA — Agent Path</b><br>
        Agent: {agent_id}<br>
        Status: <b>{status}</b><br>
        Gold: {n_gold_pts} pts, {n_gold_mv} maneuvers<br>
        Agent: {n_steps} steps<br>
        <hr style="margin:4px 0">
        <span style="color:#3388ff;">—— gold path</span><br>
        <span style="color:#e74c3c;">—— agent path</span><br>
        <span style="background:#3388ff;display:inline-block;width:8px;height:8px;border-radius:50%;"></span> maneuver point<br>
        <span style="background:#e74c3c;display:inline-block;width:8px;height:8px;border-radius:50%;"></span> decision point<br>
        ⚠ disruption on chosen route<br>
        <hr style="margin:4px 0">
        <b>Decisions:</b><br>
        {decision_lines}
    </div>
    """
    m.get_root().html.add_child(folium.Element(info_html))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    m.save(output_path)
    logger.info(f"Path map saved to {output_path}")
    return output_path
