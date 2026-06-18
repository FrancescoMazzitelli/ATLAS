import logging
import math
import subprocess
from typing import Dict, List, Optional, Tuple

from routing.traffic_csv import (
    TrafficCsvWriter,
    graph_id_to_string,
    tile_csv_path,
)
from routing.traffic_stream import build_traffic_stream as _build_stream
from routing.dct import build_historical_speeds, BUCKETS_PER_WEEK

logger = logging.getLogger(__name__)

CONGESTION_UNKNOWN = 0
CONGESTION_NO_TRAFFIC = 1
CONGESTION_FREE_FLOW = 2
CONGESTION_MODERATE = 3
CONGESTION_HEAVY = 4
CONGESTION_SEVERE = 5


class TrafficManager:
    def __init__(self, jam_density_per_km: float = 50.0):
        self.jam_density = jam_density_per_km
        self._edge_agents: Dict[int, Dict[str, Tuple[float, float]]] = {}
        self._traffic_stream: str = ""

    def record_edge(self, agent_id: str, edge_id: int,
                    speed_kph: float, edge_length_m: float):
        if edge_id not in self._edge_agents:
            self._edge_agents[edge_id] = {}
        self._edge_agents[edge_id][agent_id] = (speed_kph, edge_length_m)

    def remove_agent(self, agent_id: str):
        for edge_id in list(self._edge_agents.keys()):
            self._edge_agents[edge_id].pop(agent_id, None)
            if not self._edge_agents[edge_id]:
                del self._edge_agents[edge_id]

    def clear(self):
        self._edge_agents.clear()
        self._traffic_stream = ""

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

    def build_historical_csv(self, traffic_dir: str = "traffic") -> int:
        edge_speeds = self.compute_speeds()
        writer = TrafficCsvWriter(traffic_dir)
        return writer.write_edge_csv(edge_speeds, traffic_dir)

    def load_into_valhalla(self, traffic_dir: str = "traffic",
                           docker_container: Optional[str] = None,
                           valhalla_config: str = "/etc/valhalla/valhalla.json",
                           container_traffic_dir: str = "/traffic") -> bool:
        try:
            if docker_container:
                cmd = [
                    "docker", "cp", traffic_dir,
                    f"{docker_container}:{container_traffic_dir}"
                ]
                subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                cmd = [
                    "docker", "exec", docker_container,
                    "valhalla_add_predicted_traffic",
                    "-c", valhalla_config,
                    "-t", container_traffic_dir,
                ]
            else:
                cmd = [
                    "valhalla_add_predicted_traffic",
                    "-c", valhalla_config,
                    "-t", traffic_dir,
                ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            logger.info("Traffic data loaded into Valhalla successfully")
            return True
        except FileNotFoundError:
            logger.warning("valhalla_add_predicted_traffic not found; "
                           "install Valhalla tools or use Docker")
            return False
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to load traffic: {e.stderr.decode()}")
            return False
        except Exception as e:
            logger.warning(f"Error loading traffic: {e}")
            return False

    @property
    def traffic_stream(self) -> str:
        return self._traffic_stream

    @property
    def edge_count(self) -> int:
        return len(self._edge_agents)

    @property
    def total_agents_on_edges(self) -> int:
        return sum(len(agents) for agents in self._edge_agents.values())

    def edge_summary(self) -> List[Dict]:
        return [
            {"edge_id": eid, "n_agents": len(ag),
             "edge_length_m": next(iter(ag.values()))[1] if ag else 0}
            for eid, ag in self._edge_agents.items()
        ]


CongestionLevel = int


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
        self._zones = []
