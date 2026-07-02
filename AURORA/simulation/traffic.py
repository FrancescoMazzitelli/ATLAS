import logging
import math
import subprocess
import os
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from routing.engine import Route, RoutePoint
from routing.traffic_csv import TrafficCsvWriter
from routing.traffic_stream import build_traffic_stream as _build_stream

logger = logging.getLogger(__name__)

CONGESTION_UNKNOWN = 0
CONGESTION_NO_TRAFFIC = 1
CONGESTION_FREE_FLOW = 2
CONGESTION_MODERATE = 3
CONGESTION_HEAVY = 4
CONGESTION_SEVERE = 5

LOS_A = "A"
LOS_B = "B"
LOS_C = "C"
LOS_D = "D"
LOS_E = "E"
LOS_F = "F"


@dataclass
class EdgeInterpolation:
    edge_id: int
    begin_idx: int
    end_idx: int
    begin_point: Tuple[float, float]
    end_point: Tuple[float, float]
    length_m: float
    free_speed_kph: float
    free_flow_time_sec: float


@dataclass
class RouteInterpolator:
    shape: List[RoutePoint]
    edges: List[EdgeInterpolation] = field(default_factory=list)
    cumulative_time: List[float] = field(default_factory=list)
    total_duration_sec: float = 0.0

    @classmethod
    def from_route(cls, route: Route) -> "RouteInterpolator":
        if not route.shape or len(route.shape) < 2:
            return cls(shape=route.shape)
        pts = route.shape
        interp = cls(shape=pts)
        i = 0
        while i < len(pts):
            edge_id = getattr(pts[i], 'edge_id', 0) or 0
            j = i
            while j < len(pts) and (getattr(pts[j], 'edge_id', 0) or 0) == edge_id:
                j += 1
            if j > i:
                begin = (pts[i].lat, pts[i].lon)
                end_pt = pts[min(j, len(pts) - 1)]
                end = (end_pt.lat, end_pt.lon)
                length = sum(
                    _haversine(pts[k].lat, pts[k].lon, pts[k + 1].lat, pts[k + 1].lon)
                    for k in range(i, min(j, len(pts) - 1))
                )
                speed = getattr(pts[i], 'speed', 50) or 50
                interp.edges.append(EdgeInterpolation(
                    edge_id=edge_id if edge_id > 0 else -(i + 1),
                    begin_idx=i, end_idx=j - 1,
                    begin_point=begin, end_point=end,
                    length_m=length,
                    free_speed_kph=speed,
                    free_flow_time_sec=(length / 1000) / max(speed, 1) * 3600,
                ))
            i = j if j > i else i + 1

        cum = 0.0
        interp.cumulative_time = [0.0]
        for e in interp.edges:
            cum += e.free_flow_time_sec
            interp.cumulative_time.append(cum)
        interp.total_duration_sec = cum
        return interp

    def _walk(self, elapsed_sec: float, jam_density: float = 50.0,
              edge_densities: Optional[Dict[int, float]] = None
              ) -> Tuple[int, float, float]:
        if not self.edges:
            return (0, 0.0, 0.0)
        elapsed = elapsed_sec
        for idx, (e, t_start, t_end) in enumerate(zip(self.edges, self.cumulative_time, self.cumulative_time[1:])):
            seg_dur = t_end - t_start
            if seg_dur <= 0:
                continue
            density = (edge_densities or {}).get(e.edge_id, 0)
            ratio = density / jam_density
            speed_factor = max(0.05, 1.0 - ratio)
            actual_dur = seg_dur / max(speed_factor, 0.01)
            if elapsed <= actual_dur:
                frac = elapsed / actual_dur if actual_dur > 0 else 0
                frac = max(0.0, min(1.0, frac))
                return (idx, frac, actual_dur)
            elapsed -= actual_dur
        return (len(self.edges) - 1, 1.0, 0.0)

    def position_at(self, elapsed_sec: float, jam_density: float = 50.0,
                    edge_densities: Optional[Dict[int, float]] = None) -> Tuple[float, float]:
        if not self.edges:
            return (self.shape[-1].lat, self.shape[-1].lon) if self.shape else (0, 0)
        if elapsed_sec <= 0:
            return self.edges[0].begin_point
        idx, frac, _ = self._walk(elapsed_sec, jam_density, edge_densities)
        e = self.edges[idx]
        lat = e.begin_point[0] + (e.end_point[0] - e.begin_point[0]) * frac
        lon = e.begin_point[1] + (e.end_point[1] - e.begin_point[1]) * frac
        return (lat, lon)

    def edge_at(self, elapsed_sec: float, jam_density: float = 50.0,
                edge_densities: Optional[Dict[int, float]] = None) -> int:
        if not self.edges:
            return 0
        if elapsed_sec <= 0:
            return self.edges[0].edge_id
        if self.total_duration_sec > 0 and elapsed_sec >= self.total_duration_sec * 2:
            return self.edges[-1].edge_id
        idx, _, _ = self._walk(elapsed_sec, jam_density, edge_densities)
        return self.edges[idx].edge_id

    def progress_pct(self, elapsed_sec: float) -> float:
        if self.total_duration_sec <= 0:
            return 100.0
        return min(100.0, elapsed_sec / self.total_duration_sec * 100.0)


@dataclass
class TrafficMetricsSnapshot:
    tick: int
    timestamp: str
    edges: Dict[int, Dict] = field(default_factory=dict)
    total_vehicles: int = 0
    mean_speed_kph: float = 0.0
    mean_density_veh_per_km: float = 0.0
    los_summary: Dict[str, int] = field(default_factory=lambda: {l: 0 for l in "ABCDEF"})


class TrafficMetrics:
    def __init__(self, jam_density_per_km: float = 50.0, lanes: int = 2):
        self.jam_density = jam_density_per_km
        self.lanes = lanes
        self._edge_counts: Dict[int, Dict[str, Tuple[float, float]]] = {}

    def record_edge_occupancy(self, edge_id: int, agent_id: str,
                               speed_kph: float, edge_length_m: float):
        if edge_id not in self._edge_counts:
            self._edge_counts[edge_id] = {}
        self._edge_counts[edge_id][agent_id] = (speed_kph, edge_length_m)

    def remove_agent(self, agent_id: str):
        for edge_id in list(self._edge_counts.keys()):
            self._edge_counts[edge_id].pop(agent_id, None)
            if not self._edge_counts[edge_id]:
                del self._edge_counts[edge_id]

    def clear(self):
        self._edge_counts.clear()

    def snapshot(self, tick: int, timestamp: str) -> TrafficMetricsSnapshot:
        snap = TrafficMetricsSnapshot(tick=tick, timestamp=timestamp)
        speeds, densities, los_counts = [], [], {l: 0 for l in "ABCDEF"}

        for edge_id, agents in self._edge_counts.items():
            if not agents:
                continue
            n = len(agents)
            first_speed = next(iter(agents.values()))[0]
            edge_length = next(iter(agents.values()))[1]
            if edge_length <= 0:
                continue
            length_km = edge_length / 1000.0
            density = n / (length_km * self.lanes) if length_km > 0 else 0
            speed = first_speed * max(0.05, 1.0 - density / max(self.jam_density, 1))
            flow = density * speed
            occupancy = density / (self.jam_density * 2) * 100 if self.jam_density > 0 else 0
            los = self._los_from_density(density)

            speeds.append(speed)
            densities.append(density)
            if los in los_counts:
                los_counts[los] += 1

            snap.edges[edge_id] = {
                "edge_id": edge_id,
                "n_vehicles": n,
                "length_m": edge_length,
                "density_veh_per_km": round(density, 2),
                "speed_kph": round(speed, 1),
                "flow_veh_per_hour": round(flow, 1),
                "occupancy_pct": round(occupancy, 1),
                "los": los,
            }

        snap.total_vehicles = sum(len(a) for a in self._edge_counts.values())
        snap.mean_speed_kph = sum(speeds) / len(speeds) if speeds else 0.0
        snap.mean_density_veh_per_km = sum(densities) / len(densities) if densities else 0.0
        snap.los_summary = los_counts
        return snap

    @staticmethod
    def _los_from_density(density: float) -> str:
        if density <= 7:
            return LOS_A
        elif density <= 11:
            return LOS_B
        elif density <= 16:
            return LOS_C
        elif density <= 22:
            return LOS_D
        elif density <= 28:
            return LOS_E
        else:
            return LOS_F


class TrafficManager:
    def __init__(self, jam_density_per_km: float = 50.0,
                 docker_container: str = "",
                 valhalla_config: str = "/etc/valhalla/valhalla.json",
                 container_traffic_dir: str = "/traffic",
                 traffic_backup_dir: str = "traffic_backup"):
        self.jam_density = jam_density_per_km
        self.docker_container = docker_container
        self.valhalla_config = valhalla_config
        self.container_traffic_dir = container_traffic_dir
        self.traffic_backup_dir = traffic_backup_dir
        self._edge_agents: Dict[int, Dict[str, Tuple[float, float]]] = {}
        self._traffic_stream: str = ""
        self._has_backup = False
        self.metrics = TrafficMetrics(jam_density_per_km=jam_density_per_km)

    def backup_traffic(self) -> bool:
        if not self.docker_container:
            logger.warning("No docker container set; cannot backup traffic.tar")
            return False
        try:
            os.makedirs(self.traffic_backup_dir, exist_ok=True)
            backup_path = os.path.join(self.traffic_backup_dir, "traffic_backup.tar")
            if os.path.exists(backup_path):
                logger.info(f"Traffic backup already exists at {backup_path}")
                self._has_backup = True
                return True
            cmd = [
                "docker", "cp",
                f"{self.docker_container}:/custom_files/traffic.tar",
                backup_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            logger.info(f"Traffic backup saved to {backup_path}")
            self._has_backup = True
            return True
        except Exception as e:
            logger.warning(f"Failed to backup traffic.tar: {e}")
            return False

    def restore_backup(self) -> bool:
        if not self.docker_container or not self._has_backup:
            logger.warning("Cannot restore: no backup or no container")
            return False
        try:
            backup_path = os.path.join(self.traffic_backup_dir, "traffic_backup.tar")
            if not os.path.exists(backup_path):
                logger.warning(f"Backup not found at {backup_path}")
                return False
            cmd = [
                "docker", "cp",
                backup_path,
                f"{self.docker_container}:/custom_files/traffic.tar",
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            logger.info("Traffic backup restored to Valhalla")
            return True
        except Exception as e:
            logger.warning(f"Failed to restore traffic backup: {e}")
            return False

    def record_edge(self, agent_id: str, edge_id: int,
                    speed_kph: float, edge_length_m: float):
        if edge_id not in self._edge_agents:
            self._edge_agents[edge_id] = {}
        self._edge_agents[edge_id][agent_id] = (speed_kph, edge_length_m)
        self.metrics.record_edge_occupancy(edge_id, agent_id, speed_kph, edge_length_m)

    def remove_agent(self, agent_id: str):
        for edge_id in list(self._edge_agents.keys()):
            self._edge_agents[edge_id].pop(agent_id, None)
            if not self._edge_agents[edge_id]:
                del self._edge_agents[edge_id]
        self.metrics.remove_agent(agent_id)

    def clear(self):
        self._edge_agents.clear()
        self._traffic_stream = ""
        self.metrics.clear()

    def compute_speeds(self) -> Dict[int, Tuple[int, int, int]]:
        result = {}
        for edge_id, agents in self._edge_agents.items():
            if not agents:
                continue
            first_speed, edge_length = next(iter(agents.values()))
            if edge_length <= 0:
                continue
            n = len(agents)
            density_km = n / (edge_length / 1000.0)
            ratio = density_km / self.jam_density
            v_free = first_speed
            v_adj = v_free * max(0.05, 1.0 - ratio)

            if ratio >= 0.8:
                congestion = CONGESTION_SEVERE
            elif ratio >= 0.6:
                congestion = CONGESTION_HEAVY
            elif ratio >= 0.4:
                congestion = CONGESTION_MODERATE
            elif ratio >= 0.2:
                congestion = CONGESTION_FREE_FLOW
            else:
                congestion = CONGESTION_NO_TRAFFIC

            result[edge_id] = (int(round(v_adj)), congestion, int(round(edge_length)))
        return result

    def build_traffic_stream(self) -> str:
        edge_speeds = self.compute_speeds()
        self._traffic_stream = _build_stream(edge_speeds) if edge_speeds else ""
        return self._traffic_stream

    def edge_density_map(self) -> Dict[int, float]:
        result = {}
        for edge_id, agents in self._edge_agents.items():
            if not agents or not next(iter(agents.values()))[1]:
                continue
            n = len(agents)
            length_m = next(iter(agents.values()))[1]
            result[edge_id] = n / (length_m / 1000.0)
        return result

    def save_metrics(self, snapshots: List[TrafficMetricsSnapshot], output_dir: str):
        path = os.path.join(output_dir, "traffic_metrics.json")
        data = {
            "n_snapshots": len(snapshots),
            "snapshots": [vars(s) for s in snapshots],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Traffic metrics saved to {path} ({len(snapshots)} snapshots)")

        csv_path = os.path.join(output_dir, "traffic_metrics.csv")
        import csv
        rows = []
        for s in snapshots:
            for eid, e in s.edges.items():
                rows.append({
                    "tick": s.tick,
                    "timestamp": s.timestamp,
                    "edge_id": eid,
                    **e,
                })
        if rows:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

    def edge_summary(self) -> List[Dict]:
        return [
            {"edge_id": eid, "n_agents": len(ag),
             "edge_length_m": next(iter(ag.values()))[1] if ag else 0}
            for eid, ag in self._edge_agents.items()
        ]

    @property
    def traffic_stream(self) -> str:
        return self._traffic_stream

    @property
    def edge_count(self) -> int:
        return len(self._edge_agents)

    @property
    def total_agents_on_edges(self) -> int:
        return sum(len(agents) for agents in self._edge_agents.values())


class CongestionTracker:
    def __init__(self, grid_size_meters: float = 500.0,
                 min_agents_for_cluster: int = 3,
                 cluster_radius_meters: float = 1000.0):
        self.grid_size = grid_size_meters
        self.min_cluster = min_agents_for_cluster
        self.cluster_radius = cluster_radius_meters
        self._positions: Dict[str, Tuple[float, float]] = {}
        self._zones: List[Dict] = []

    def add_agent(self, agent_id: str, lat: float, lon: float):
        self._positions[agent_id] = (lat, lon)

    def remove_agent(self, agent_id: str):
        self._positions.pop(agent_id, None)

    def update(self, agent_positions: Dict[str, Tuple[float, float]]):
        self._positions.update(agent_positions)
        stale = [aid for aid in self._positions if aid not in agent_positions]
        for aid in stale:
            self._positions.pop(aid, None)
        self._recluster()

    def _recluster(self):
        if len(self._positions) < self.min_cluster:
            self._zones = []
            return
        grid: Dict[Tuple[int, int], List[str]] = {}
        for aid, (lat, lon) in self._positions.items():
            cx = int(lat * 111_000 / self.grid_size)
            cy = int(lon * 111_000 / self.grid_size)
            grid.setdefault((cx, cy), []).append(aid)
        zones = []
        for (cx, cy), agents in grid.items():
            if len(agents) < self.min_cluster:
                continue
            lats = [self._positions[a][0] for a in agents]
            lons = [self._positions[a][1] for a in agents]
            center_lat = sum(lats) / len(lats)
            center_lon = sum(lons) / len(lons)
            n = len(agents)
            severity = (
                "severe" if n >= 10 else
                "heavy" if n >= 6 else
                "moderate" if n >= 4 else
                "light"
            )
            zones.append({
                "lat": center_lat, "lon": center_lon,
                "radius_meters": self.cluster_radius,
                "n_agents": n, "severity": severity,
            })
        self._zones = zones

    @property
    def zones(self) -> List[Dict]:
        return self._zones

    @property
    def avoid_locations(self) -> List[Tuple[float, float]]:
        return [(z["lat"], z["lon"]) for z in self._zones]

    def clear(self):
        self._positions.clear()
        self._zones.clear()


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
