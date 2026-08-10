import re
import json
import glob
import os
import geopandas as gpd
from shapely.geometry import LineString


def _search1(pattern, content, cast=str):
    m = re.search(pattern, content)
    return cast(m.group(1)) if m else None


def _parse_polylines(content):
    """poly_id -> {'coords': [[lat, lon], ...], 'style': {...}}"""
    pattern = re.compile(
        r'var poly_line_(\w+) = L\.polyline\(\s*(\[\[.*?\]\])\s*,\s*(\{.*?\})\s*\)\.addTo\(map_\w+\);',
        re.DOTALL,
    )
    out = {}
    for poly_id, coords_str, style_str in pattern.findall(content):
        try:
            coords = json.loads(coords_str)
            style = json.loads(style_str)
        except json.JSONDecodeError:
            continue
        out[poly_id] = {"coords": coords, "style": style}
    return out


def _parse_trip_labels(content):
    """html_id -> (path_type, trip_num, pts)  from the popup div text itself"""
    pattern = re.compile(
        r'<div id="html_(\w+)"[^>]*>(Gold|Agent) path trip (\d+) \((\d+) pts\)</div>'
    )
    return {
        html_id: (ptype.lower(), int(tnum), int(pts))
        for html_id, ptype, tnum, pts in pattern.findall(content)
    }


def _parse_popup_to_html(content):
    """popup_id -> html_id, from popup_X.setContent(html_Y);"""
    pattern = re.compile(r'popup_(\w+)\.setContent\(html_(\w+)\);')
    return dict(pattern.findall(content))


def _parse_polyline_to_popup(content):
    """poly_id -> popup_id, from poly_line_X.bindPopup(popup_Y)"""
    pattern = re.compile(r'poly_line_(\w+)\.bindPopup\(popup_(\w+)\)')
    return dict(pattern.findall(content))


def parse_aurora_html(filepath):
    """Parse a single AURORA path-plot HTML file into a list of trip-leg dict rows."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    agent_id = _search1(r'Agent:\s*(\w+)<br>', content)
    status = _search1(r'Status:\s*<b>(\w+)</b>', content)
    gold_pts_total = _search1(r'Gold:\s*(\d+)\s*pts,\s*\d+\s*maneuvers', content, int)
    gold_maneuvers = _search1(r'Gold:\s*\d+\s*pts,\s*(\d+)\s*maneuvers', content, int)
    agent_steps_total = _search1(r'Agent:\s*(\d+)\s*steps', content, int)

    polylines = _parse_polylines(content)
    labels = _parse_trip_labels(content)
    popup_to_html = _parse_popup_to_html(content)
    poly_to_popup = _parse_polyline_to_popup(content)

    rows = []
    for poly_id, data in polylines.items():
        popup_id = poly_to_popup.get(poly_id)
        html_id = popup_to_html.get(popup_id) if popup_id else None
        label = labels.get(html_id) if html_id else None
        if label is None:
            continue  # not a "trip N" polyline (shouldn't happen for these files)

        path_type, trip_num, pts = label
        coords = data["coords"]  # [[lat, lon], ...]
        color = data["style"].get("color")

        line = LineString([(lon, lat) for lat, lon in coords])  # (x=lon, y=lat)

        rows.append({
            "agent_id": agent_id,
            "status": status,
            "gold_pts_total": gold_pts_total,
            "gold_maneuvers": gold_maneuvers,
            "agent_steps_total": agent_steps_total,
            "path_type": path_type,      # 'gold' or 'agent'
            "trip_num": trip_num,        # 1..N leg index
            "trip_pts": pts,             # points for this specific leg
            "n_points": len(coords),
            "color": color,
            "source_file": os.path.basename(filepath),
            "geometry": line,
        })
    return rows


def build_trip_geodataframe(folder_path, pattern="*.html"):
    """
    Point this at a folder of AURORA path-plot HTML files and get back a
    GeoDataFrame with one row per trip-leg LineString (gold or agent).
    """
    rows = []
    for fp in sorted(glob.glob(os.path.join(folder_path, pattern))):
        try:
            rows.extend(parse_aurora_html(fp))
        except Exception as e:
            print(f"Warning: failed to parse {fp}: {e}")

    if not rows:
        return gpd.GeoDataFrame(
            columns=["agent_id", "path_type", "trip_num", "trip_pts", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf = gdf.sort_values(["agent_id", "path_type", "trip_num"]).reset_index(drop=True)
    return gdf


# Example usage:
# gdf = build_trip_geodataframe("/path/to/aurora_html_folder")
# gdf.head()


import geopandas as gpd
import pandas as pd
import momepy
import numpy as np
import networkx as nx
from dotenv import load_dotenv
from pathlib import Path
import matplotlib.pyplot as plt
import os


def trim_trips_to_network(network_gdf: gpd.GeoDataFrame, trajectories_df: pd.DataFrame) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    # filter network to only those traversed by trajectories
    trajectories_links_set = set(trajectories_df.way_id) & set(network_gdf.osm_id)
    network_gdf = network_gdf[network_gdf.osm_id.isin(trajectories_links_set)]

    # make sure rows are in link-traversal order within each trip
    df = trajectories_df.sort_values(["trip_id", "traj_idx", "way_idx"]).reset_index(drop=True)
    on_network = df.way_id.isin(trajectories_links_set)

    # start a new "block" whenever trip_id changes OR on_network value changes
    new_block = (df.trip_id != df.trip_id.shift()) | (on_network != on_network.shift())
    block_id = new_block.cumsum()

    # summarize each block: which trip it belongs to, whether it's in-network, and its length
    block_info = pd.DataFrame({"trip_id": df.trip_id, "on_network": on_network, "block_id": block_id})
    block_summary = block_info.groupby("block_id").agg(
        trip_id=("trip_id", "first"),
        on_network=("on_network", "first"),
        length=("trip_id", "size"),
    )

    # for each trip, find the single longest in-network block (one path per agent)
    in_network_blocks = block_summary[block_summary.on_network]
    best_block_ids = in_network_blocks.loc[
        in_network_blocks.groupby("trip_id")["length"].idxmax()
    ].index

    keep = block_id.isin(best_block_ids)
    trajectories_df = df[keep].copy()

    # path_idx: 0-based position within the kept (trimmed) path per trip
    trajectories_df["path_idx"] = trajectories_df.groupby("trip_id").cumcount()

    return network_gdf, trajectories_df


def largest_component_gdf(gdf, multigraph=False):
    """
    Convert a LineString GeoDataFrame to a graph via momepy,
    keep only the largest connected component, and return it
    as a GeoDataFrame of edges (the street segments).
    """
    G = momepy.gdf_to_nx(gdf, approach="primal", multigraph=multigraph)

    largest_cc = max(nx.connected_components(G), key=len)
    G_largest = G.subgraph(largest_cc).copy()

    edges = momepy.nx_to_gdf(G_largest, points=False, lines=True)
    return edges


def find_disconnected_trips(osm_gdf, trips_df, osm_id_col="osm_id", way_id_col="way_id",
                             trip_id_col="trip_id", path_idx_col="path_idx",
                             snap_tolerance=0.5):
    """
    Vectorized check for connectivity gaps in trips (no per-group Python loop).

    osm_gdf: GeoDataFrame (projected CRS, meters) with columns [osm_id_col, geometry]
    trips_df: DataFrame with columns [trip_id_col, way_id_col, path_idx_col]
    snap_tolerance: meters within which two endpoints are treated as the same node

    Returns a DataFrame: trip_id, path_idx, way_id_1, way_id_2, reason
    Empty DataFrame means everything is connected.
    """

    # --- 1. Build a small way-level endpoint table (way count << trip count, so this is cheap) ---
    def first_last_xy(geom):
        if geom is None or geom.is_empty:
            return (np.nan, np.nan, np.nan, np.nan)
        c = geom.coords
        x0, y0 = c[0]
        x1, y1 = c[-1]
        return (x0, y0, x1, y1)

    xy = osm_gdf.geometry.apply(first_last_xy)
    xy_arr = np.array(xy.tolist())  # shape (n_ways, 4): x0,y0,x1,y1

    def grid_key(x, y):
        gx = np.round(x / snap_tolerance)
        gy = np.round(y / snap_tolerance)
        # combine into one int64 key; NaNs pass through as NaN until cast
        key = gx * 1_000_000_007 + gy
        return key

    node_start_key = grid_key(xy_arr[:, 0], xy_arr[:, 1])
    node_end_key = grid_key(xy_arr[:, 2], xy_arr[:, 3])

    way_table = pd.DataFrame({
        osm_id_col: osm_gdf[osm_id_col].values,
        "node_start_key": node_start_key,
        "node_end_key": node_end_key,
    }).drop_duplicates(subset=osm_id_col)  # guard against duplicate osm_ids

    start_map = dict(zip(way_table[osm_id_col], way_table["node_start_key"]))
    end_map = dict(zip(way_table[osm_id_col], way_table["node_end_key"]))

    # --- 2. Vectorized trip processing ---
    trips = trips_df.sort_values([trip_id_col, path_idx_col]).reset_index(drop=True)

    node_start = trips[way_id_col].map(start_map)
    node_end = trips[way_id_col].map(end_map)

    next_trip = trips[trip_id_col].shift(-1)
    next_way = trips[way_id_col].shift(-1)
    next_node_start = node_start.shift(-1)
    next_node_end = node_end.shift(-1)

    same_trip = trips[trip_id_col].values == next_trip.values

    # any missing endpoint on either side -> can't confirm connection -> missing_way_id
    unknown = node_start.isna() | node_end.isna() | next_node_start.isna() | next_node_end.isna()

    connected = (
        (node_start == next_node_start) | (node_start == next_node_end) |
        (node_end == next_node_start) | (node_end == next_node_end)
    )

    gap_mask = same_trip & (~connected.values | unknown.values)

    reason = np.where(unknown.values, "missing_way_id", "no_shared_node")

    gaps_df = pd.DataFrame({
        "trip_id": trips[trip_id_col].values[gap_mask],
        "path_idx": trips[path_idx_col].values[gap_mask],
        "way_id_1": trips[way_id_col].values[gap_mask],
        "way_id_2": next_way.values[gap_mask],
        "reason": reason[gap_mask],
    })

    return gaps_df


def filter_by_min_path_count(trips_df, min_links, trip_id_col="trip_id", path_idx_col="path_idx"):
    """
    Filters out trips where max(path_idx) < min_links.

    trips_df: DataFrame with trip_id_col and path_idx_col
    min_links: minimum max(path_idx) a trip must have to be kept

    Returns: filtered trips_df (only rows belonging to trips that pass the threshold)
    """
    max_idx = trips_df.groupby(trip_id_col)[path_idx_col].transform("max")
    return trips_df[max_idx >= min_links]


def plot_random_trips(osm_gdf, trips_df, n=5, min_path_count=3,
                       osm_id_col="osm_id", trip_id_col="trip_id",
                       way_id_col="way_id", path_idx_col="path_idx",
                       random_state=None):
    """
    Plots n randomly sampled trips (each above min_path_count) on a single map,
    each trip in a distinct color, over the network as background.
    """
    # filter to eligible trips
    max_idx = trips_df.groupby(trip_id_col)[path_idx_col].transform("max")
    eligible = trips_df[max_idx >= min_path_count]

    trip_ids = eligible[trip_id_col].unique()
    if len(trip_ids) == 0:
        print("No trips meet the min_path_count threshold.")
        return

    rng = np.random.default_rng(random_state)
    sample_ids = rng.choice(trip_ids, size=min(n, len(trip_ids)), replace=False)

    way_geom = osm_gdf.set_index(osm_id_col).geometry
    colors = plt.get_cmap("tab10", len(sample_ids))

    fig, ax = plt.subplots(figsize=(10, 10))
    osm_gdf.plot(ax=ax, color="lightgray", linewidth=0.5, zorder=1)

    for i, trip_id in enumerate(sample_ids):
        trip_rows = eligible[eligible[trip_id_col] == trip_id].sort_values(path_idx_col)
        way_ids = trip_rows[way_id_col].tolist()

        valid_ways = [w for w in way_ids if w in way_geom.index]
        if not valid_ways:
            continue

        trip_geoms = way_geom.loc[valid_ways].reset_index()
        trip_geoms.plot(ax=ax, color=colors(i), linewidth=2.5, zorder=2,
                         label=f"trip_id={trip_id} (n_links={len(way_ids)})")

    ax.legend(loc="best", fontsize=8)
    ax.set_axis_off()
    plt.tight_layout()
    plt.show()