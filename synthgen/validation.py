import re
import json
import os
import glob
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point


def _js_obj_to_json(js_str):
    s = (js_str
         .replace("True", "true")
         .replace("False", "false")
         .replace("None", "null"))
    s = re.sub(r',\s*([\}\]])', r'\1', s)
    return s


def extract_agent_idx(html_text):
    """Extract numeric agent_idx from 'Agent: agent_0003<br>' -> 3 (matches trips.csv agent_idx)."""
    agent_match = re.search(r'Agent:\s*agent_0*(\d+)\s*<br>', html_text)
    return int(agent_match.group(1)) if agent_match else None


def extract_polylines(html_text):
    """Extract full-tour polylines (gold + agent), no dedup, tagged by path_type.
    Keeps raw [lat, lon] coords so we can slice them by trip boundary later."""
    pattern = re.compile(
        r'L\.polyline\(\s*(\[\[.*?\]\])\s*,\s*(\{.*?\})\s*\)\.addTo',
        re.DOTALL
    )
    results = []
    for coords_str, style_str in pattern.findall(html_text):
        coords = json.loads(_js_obj_to_json(coords_str))  # [[lat, lon], ...]
        style = json.loads(_js_obj_to_json(style_str))
        color = style.get("color")
        path_type = "gold" if color == "#3388ff" else "agent"
        results.append({"path_type": path_type, "coords": coords})
    return results


def _nearest_index(coords_latlon, target_lonlat, max_deg=0.0005):
    """
    coords_latlon: list of [lat, lon]
    target_lonlat: (lon, lat) -- matches trips.csv (o_x/d_x, o_y/d_y) convention
    Returns index of nearest point, or None if the closest point exceeds max_deg
    (~55m at this latitude) -- a safety check in case a boundary point isn't
    exactly present (e.g. HTML rounding, or a route deviation at the endpoint).
    """
    arr = np.asarray(coords_latlon)  # (N, 2) -> lat, lon
    tgt_lat, tgt_lon = target_lonlat[1], target_lonlat[0]
    d2 = (arr[:, 0] - tgt_lat) ** 2 + (arr[:, 1] - tgt_lon) ** 2
    idx = int(np.argmin(d2))
    if d2[idx] ** 0.5 > max_deg:
        return None
    return idx


def split_polyline_by_trips(coords_latlon, trips_sub, max_deg=0.0005):
    """
    coords_latlon: full-tour coords [[lat,lon], ...] for ONE polyline (gold or agent)
    trips_sub: this agent's trips.csv rows, sorted by trip_id
    Returns list of dicts: {trip_id, geometry (LineString, lon/lat), n_points}
    """
    segments = []
    start_idx = 0
    n = len(trips_sub)

    for i, row in enumerate(trips_sub.itertuples()):
        if i == n - 1:
            end_idx = len(coords_latlon) - 1  # last trip runs to the end of the tour
        else:
            target = (row.d_x, row.d_y)  # (lon, lat)
            found_idx = _nearest_index(coords_latlon, target, max_deg=max_deg)
            if found_idx is None or found_idx <= start_idx:
                print(f"  Warning: couldn't locate boundary for trip_id={row.trip_id}, "
                      f"defaulting to end of polyline")
                end_idx = len(coords_latlon) - 1
            else:
                end_idx = found_idx

        chunk = coords_latlon[start_idx:end_idx + 1]
        if len(chunk) < 2:
            chunk = coords_latlon[max(0, start_idx - 1):end_idx + 1]

        line = LineString([(lon, lat) for lat, lon in chunk])
        segments.append({"trip_id": row.trip_id, "geometry": line, "n_points": len(chunk)})
        start_idx = end_idx

    return segments


def process_files_from_folder(folder_path, glob_pattern, trips_csv_path):
    """
    Find files in folder_path matching glob_pattern, split each agent's
    gold/agent tour polyline into per-trip segments using trips_csv_path,
    and build origin/destination points directly from the trip list
    (far more reliable than parsing the single play/flag marker pair,
    which only exists once per file regardless of trip count).

    Returns:
        (lines_gdf, points_gdf) tuple of GeoDataFrames, each with a trip_idx column.
    """
    search_path = os.path.join(folder_path, glob_pattern)
    filepaths = glob.glob(search_path)

    if not filepaths:
        print(f"No files matched: {search_path}")
        return None, None

    trips_df = pd.read_csv(trips_csv_path)

    all_lines = []
    all_points = []

    for filepath in filepaths:
        fname = os.path.splitext(os.path.basename(filepath))[0]
        with open(filepath, "r", encoding="utf-8") as f:
            html = f.read()

        agent_idx = extract_agent_idx(html)
        if agent_idx is None:
            print(f"Warning: could not find agent id in {filepath}")
            continue

        trips_sub = trips_df[trips_df["agent_idx"] == agent_idx].sort_values("trip_id")
        if trips_sub.empty:
            print(f"Warning: no trips.csv rows found for agent_idx={agent_idx} ({filepath})")
            continue

        raw_polylines = extract_polylines(html)
        if not raw_polylines:
            print(f"Warning: no polylines found in {filepath}")

        for poly in raw_polylines:
            segments = split_polyline_by_trips(poly["coords"], trips_sub)
            for seg in segments:
                all_lines.append({
                    "agent_idx": agent_idx,
                    "trip_idx": seg["trip_id"],
                    "path_type": poly["path_type"],
                    "n_points": seg["n_points"],
                    "geometry": seg["geometry"],
                    "source_file": fname,
                })

        for row in trips_sub.itertuples():
            all_points.append({
                "agent_idx": agent_idx, "trip_idx": row.trip_id,
                "point_type": "origin", "purpose": row.origin,
                "geometry": Point(row.o_x, row.o_y), "source_file": fname,
            })
            all_points.append({
                "agent_idx": agent_idx, "trip_idx": row.trip_id,
                "point_type": "destination", "purpose": row.destination,
                "geometry": Point(row.d_x, row.d_y), "source_file": fname,
            })

    lines_gdf = gpd.GeoDataFrame(all_lines, crs="EPSG:4326") if all_lines else None
    points_gdf = gpd.GeoDataFrame(all_points, crs="EPSG:4326") if all_points else None

    return lines_gdf, points_gdf