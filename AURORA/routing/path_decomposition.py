import math
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import time

from routing.engine import Route, RoutePoint

logger = logging.getLogger(__name__)


@dataclass
class MicroStep:
    step_id: str
    origin_idx: int
    destination_idx: int
    distance_km: float
    estimated_duration_sec: float
    road_name: Optional[str] = None
    geometry: List[RoutePoint] = field(default_factory=list)


@dataclass
class PathSegment:
    segment_id: str
    origin: Tuple[float, float]
    destination: Tuple[float, float]
    purpose: str
    mode: str
    departure_time: time
    full_route: Optional[Route] = None
    micro_steps: List[MicroStep] = field(default_factory=list)
    alternatives: List[Route] = field(default_factory=list)
    selected: Optional[str] = None

    def to_dict(self) -> Dict:
        return {"segment_id": self.segment_id, "origin": {"lat": self.origin[0], "lon": self.origin[1]},
                "destination": {"lat": self.destination[0], "lon": self.destination[1]},
                "purpose": self.purpose, "mode": self.mode,
                "departure_time": self.departure_time.strftime("%H:%M"),
                "n_micro_steps": len(self.micro_steps), "n_alternatives": len(self.alternatives),
                "selected": self.selected}


class PathDecomposer:
    def __init__(self, step_km: float = 1.0):
        self.step_km = step_km

    def decompose(self, route: Route, segment_id: str) -> List[MicroStep]:
        if not route.shape:
            return []
        points = route.shape
        steps, acc, seg_pts, seg_start, idx = [], 0.0, [], 0, 0

        for i in range(1, len(points)):
            d = self._haversine(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon)
            acc += d
            seg_pts.append(points[i])
            if acc >= self.step_km or i == len(points) - 1:
                if seg_pts:
                    steps.append(MicroStep(
                        step_id=f"{segment_id}_step_{idx}", origin_idx=seg_start, destination_idx=i,
                        distance_km=round(acc, 3),
                        estimated_duration_sec=(acc / max(route.length_km, 0.1)) * route.duration_seconds if route.length_km > 0 else 0,
                        geometry=seg_pts,
                    ))
                seg_pts, acc, seg_start, idx = [], 0.0, i, idx + 1

        logger.info(f"Decomposed {segment_id}: {len(points)} points -> {len(steps)} micro-steps")
        return steps

    def build(self, route: Route, origin: Tuple[float, float], destination: Tuple[float, float],
              purpose: str, departure_time: time, alternatives: Optional[List[Route]] = None) -> PathSegment:
        seg = PathSegment(segment_id=f"seg_{purpose}_{departure_time.strftime('%H%M')}",
                          origin=origin, destination=destination, purpose=purpose,
                          mode=route.mode, departure_time=departure_time,
                          full_route=route, alternatives=alternatives or [route])
        seg.micro_steps = self.decompose(route, seg.segment_id)
        return seg

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R, dlat, dlon = 6371.0, math.radians(lat2 - lat1), math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
