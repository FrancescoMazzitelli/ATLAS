import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from routing.engine import ValhallaEngine, Route, Costing

logger = logging.getLogger(__name__)


@dataclass
class GoldPath:
    origin: Tuple[float, float]
    destination: Tuple[float, float]
    waypoints: List[Tuple[float, float]] = field(default_factory=list)
    total_length_km: float = 0.0
    total_duration_sec: float = 0.0
    segments: List[Route] = field(default_factory=list)

    def deviation(self, position: Tuple[float, float]) -> float:
        if not self.waypoints:
            return 0.0
        return min(
            ((position[0] - wp[0])**2 + (position[1] - wp[1])**2)**0.5 * 111_000
            for wp in self.waypoints
        )

    def progress(self, position: Tuple[float, float]) -> float:
        if not self.waypoints:
            return 0.0
        closest = min(range(len(self.waypoints)),
                      key=lambda i: ((position[0] - self.waypoints[i][0])**2 +
                                     (position[1] - self.waypoints[i][1])**2))
        return closest / len(self.waypoints) * 100

    def next_waypoint(self, position: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        if not self.waypoints:
            return None
        best_idx = min(range(len(self.waypoints)),
                       key=lambda i: ((position[0] - self.waypoints[i][0])**2 +
                                      (position[1] - self.waypoints[i][1])**2))
        next_idx = best_idx + 1
        if next_idx >= len(self.waypoints):
            return self.destination
        return self.waypoints[next_idx]

    def to_text(self, position: Optional[Tuple[float, float]] = None) -> str:
        lines = [f"Gold Path: {self.origin} -> {self.destination}",
                 f"Segments: {len(self.segments)}, Total: {self.total_length_km:.1f} km, {self.total_duration_sec / 60:.0f} min"]
        if position:
            lines.append(f"Deviation: {self.deviation(position):.0f} m, Progress: {self.progress(position):.0f}%")
        for i, s in enumerate(self.segments):
            lines.append(f"  Leg {i+1}: {s.description} ({s.length_km:.1f} km, {s.duration_seconds / 60:.0f} min)")
        return "\n".join(lines)


class GoldPathGenerator:
    def __init__(self, valhalla: ValhallaEngine):
        self.valhalla = valhalla

    def generate(self, origin: Tuple[float, float], destination: Tuple[float, float],
                 n_waypoints: int = 0) -> GoldPath:
        route = self.valhalla.route(origin, destination)
        if route is None:
            return self._synthetic(origin, destination)

        waypoints = [(p.lat, p.lon) for p in route.shape]

        if n_waypoints > 0 and len(waypoints) > n_waypoints + 1:
            step = len(waypoints) // (n_waypoints + 1)
            waypoints = [waypoints[i * step] for i in range(n_waypoints + 1)]
            waypoints[-1] = (destination[0], destination[1])

        return GoldPath(
            origin=origin, destination=destination,
            waypoints=waypoints,
            total_length_km=route.length_km,
            total_duration_sec=route.duration_seconds,
            segments=[route],
        )

    def _synthetic(self, origin: Tuple[float, float], destination: Tuple[float, float]) -> GoldPath:
        lat_step = (destination[0] - origin[0]) / 5
        lon_step = (destination[1] - origin[1]) / 5
        waypoints = [(origin[0] + lat_step * i, origin[1] + lon_step * i) for i in range(6)]
        dist = ((destination[0] - origin[0])**2 + (destination[1] - origin[1])**2)**0.5 * 111_000
        return GoldPath(
            origin=origin, destination=destination, waypoints=waypoints,
            total_length_km=dist / 1000, total_duration_sec=dist / 13.9,
        )
