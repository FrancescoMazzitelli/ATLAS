import csv
import math
import os
from typing import Dict, List, Optional, Tuple

from routing.dct import build_historical_speeds, BUCKETS_PER_WEEK

GRAPHID_LEVEL_BITS = 3
GRAPHID_TILEID_BITS = 22
GRAPHID_ID_BITS = 21

GRAPHID_LEVEL_MASK = (1 << GRAPHID_LEVEL_BITS) - 1
GRAPHID_TILEID_MASK = (1 << GRAPHID_TILEID_BITS) - 1
GRAPHID_ID_MASK = (1 << GRAPHID_ID_BITS) - 1


def graph_id_components(value: int) -> Tuple[int, int, int]:
    level = value & GRAPHID_LEVEL_MASK
    tileid = (value >> 3) & GRAPHID_TILEID_MASK
    idx = (value >> (3 + GRAPHID_TILEID_BITS)) & GRAPHID_ID_MASK
    return level, tileid, idx


def graph_id_to_string(value: int) -> str:
    level, tileid, idx = graph_id_components(value)
    return f"{level}/{tileid}/{idx}"


def tile_path(edge_id_str: str, tile_dir: str, suffix: str = ".csv") -> str:
    parts = edge_id_str.split("/")
    level = parts[0]
    tile_id_str = parts[1]
    tile_id_int = int(tile_id_str)
    tile_id_len = len(tile_id_str)
    max_length = math.ceil(tile_id_len / 3) * 3
    padded = tile_id_str.zfill(max_length)
    groups = [padded[i:i + 3] for i in range(0, max_length, 3)]
    subdirs = [f"{level}"] + groups[:-1]
    filename = f"{groups[-1]}{suffix}"
    return os.path.join(tile_dir, *subdirs, filename)


def graph_id_tile_key(edge_id_str: str) -> str:
    """Return the tile-level key (level/tileid) from an edge_id_str like '1/47701/130'."""
    parts = edge_id_str.split("/")
    return f"{parts[0]}/{parts[1]}"


def tile_csv_path(level: int, tileid: int, traffic_dir: str) -> str:
    tile_id_str = str(tileid)
    tile_id_len = len(tile_id_str)
    max_length = math.ceil(tile_id_len / 3) * 3
    padded = tile_id_str.zfill(max_length)
    groups = [padded[i:i + 3] for i in range(0, max_length, 3)]
    subdirs = [f"{level}"] + groups[:-1]
    filename = f"{groups[-1]}.csv"
    return os.path.join(traffic_dir, *subdirs, filename)


class TrafficCsvWriter:
    def __init__(self, traffic_dir: str = "traffic"):
        self.traffic_dir = traffic_dir

    def build_constant_speeds(self, freeflow_speed: float,
                              constrained_speed: float) -> List[float]:
        speeds = [freeflow_speed] * BUCKETS_PER_WEEK
        return speeds

    def build_time_varying_speeds(
        self,
        freeflow_speed: float,
        constrained_speed: float,
        peak_hours: Optional[List[int]] = None,
        off_peak_reduction: float = 0.0,
    ) -> List[float]:
        if peak_hours is None:
            peak_hours = [7, 8, 9, 16, 17, 18]
        speeds = []
        for day in range(7):
            for hour in range(24):
                is_peak = hour in peak_hours
                speed = constrained_speed if is_peak else freeflow_speed
                for _ in range(12):
                    speeds.append(float(speed))
        return speeds

    def write_tile_csv(self, edges: List[Dict], output_path: str):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["edge_id", "freeflow_speed", "constrained_speed",
                             "historical_speeds"])
            for edge in edges:
                edge_id = edge["edge_id"]
                freeflow = edge.get("freeflow_speed", edge.get("speed", 50))
                constrained = edge.get("constrained_speed", edge.get("speed", 50))
                historical = edge.get("historical_speeds")
                if historical is None:
                    speeds_list = edge.get("speeds")
                    if speeds_list is None:
                        speeds_list = self.build_constant_speeds(freeflow, constrained)
                    historical = build_historical_speeds(speeds_list)
                writer.writerow([edge_id, int(freeflow), int(constrained), historical])

    def build_tile_hierarchy(
        self,
        edge_data: Dict[str, Dict],
    ) -> Dict[str, int]:
        tiles: Dict[str, List[Dict]] = {}
        for edge_id_str, data in edge_data.items():
            tile_key = graph_id_tile_key(edge_id_str)
            if tile_key not in tiles:
                tiles[tile_key] = []
            tiles[tile_key].append({"edge_id": edge_id_str, **data})

        written = {}
        for tile_key, edges in tiles.items():
            parts = tile_key.split("/")
            level, tileid = int(parts[0]), int(parts[1])
            path = tile_csv_path(level, tileid, self.traffic_dir)
            self.write_tile_csv(edges, path)
            written[tile_key] = len(edges)

        return written

    def write_edge_csv(
        self,
        edge_speeds: Dict[int, Tuple[int, int, int]],
        tile_dir: str,
    ):
        grouped: Dict[str, list] = {}
        for numeric_id, (speed, congestion, length_m) in edge_speeds.items():
            edge_id_str = graph_id_to_string(numeric_id)
            tile_key = graph_id_tile_key(edge_id_str)
            if tile_key not in grouped:
                grouped[tile_key] = []
            grouped[tile_key].append({
                "edge_id": edge_id_str,
                "speed": speed,
                "freeflow_speed": speed,
                "constrained_speed": speed,
            })

        written = 0
        for tile_key, edges in grouped.items():
            parts = tile_key.split("/")
            level, tileid = int(parts[0]), int(parts[1])
            path = tile_csv_path(level, tileid, tile_dir)
            self.write_tile_csv(edges, path)
            written += len(edges)

        return written
