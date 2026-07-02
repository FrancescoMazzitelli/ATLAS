import requests
import json
import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# A congestion zone: (lat, lon, radius_meters, n_agents)
CongestionZone = Tuple[float, float, float, int]


@dataclass
class RoutePoint:
    lat: float
    lon: float
    speed: Optional[float] = None
    edge_id: Optional[int] = None


@dataclass
class Route:
    route_id: str
    mode: str
    duration_seconds: float
    length_km: float
    shape: List[RoutePoint]
    congestion_level: str
    has_roadblocks: bool
    has_traffic_delay: bool
    description: str
    maneuvers: List[Dict] = field(default_factory=list)

    def to_context(self) -> Dict:
        return {
            "id": self.route_id,
            "mode": self.mode,
            "duration_minutes": round(self.duration_seconds / 60, 1),
            "distance_km": round(self.length_km, 1),
            "congestion_level": self.congestion_level,
            "has_roadblocks": self.has_roadblocks,
            "has_traffic_delay": self.has_traffic_delay,
            "description": self.description,
        }


class Costing(str, Enum):
    AUTO = "auto"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"
    TRANSIT = "transit"
    TRUCK = "truck"


def _decode_next(encoded: str, start: int) -> Tuple[int, int]:
    result, shift, index = 0, 0, start
    while index < len(encoded):
        b = ord(encoded[index]) - 63
        index += 1
        result |= (b & 0x1f) << shift
        shift += 5
        if b < 0x20:
            break
    delta = ~(result >> 1) if (result & 1) else (result >> 1)
    return delta, index


def decode_polyline6(encoded: str) -> List[Tuple[float, float]]:
    if not encoded:
        return []
    points, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        dl, index = _decode_next(encoded, index)
        lat += dl
        dl, index = _decode_next(encoded, index)
        lon += dl
        points.append((lat / 1e6, lon / 1e6))
    return points


class ValhallaEngine:
    def __init__(self, host: str = "localhost", port: int = 8002, timeout: int = 30):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        logger.info(f"Valhalla routing engine: {self.base_url}")

    def route(self, origin: Tuple[float, float], destination: Tuple[float, float],
              costing: Costing = Costing.AUTO,
              avoid: Optional[List[Tuple[float, float]]] = None,
              traffic_stream: Optional[str] = None,
              depart_now: bool = False, disable_hierarchy: bool = False,
              extra_opts: Optional[Dict] = None) -> Optional[Route]:
        payload = {
            "locations": [{"lat": origin[0], "lon": origin[1]}, {"lat": destination[0], "lon": destination[1]}],
            "costing": costing.value,
            "directions_options": {"units": "km", "language": "en-US"},
            "shape_format": "polyline6",
            "shape_attributes": ["edge.id", "edge.speed", "edge.length"],
        }
        if avoid:
            payload["avoid_locations"] = [{"lat": lat, "lon": lon} for lat, lon in avoid]
        if traffic_stream:
            payload["traffic"] = {"traffic_stream": traffic_stream}
            payload.setdefault("costing_options", {}).setdefault("auto", {})["use_traffic"] = True
        if depart_now:
            payload["date_time"] = {"type": 0}
        if disable_hierarchy:
            payload.setdefault("costing_options", {})[costing.value] = {"disable_hierarchy_pruning": True}
        if extra_opts:
            payload.setdefault("costing_options", {}).setdefault(costing.value, {}).update(extra_opts)

        try:
            resp = requests.post(f"{self.base_url}/route", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return self._parse(resp.json(), costing)
        except requests.exceptions.ConnectionError:
            logger.warning(f"Valhalla unreachable at {self.base_url}")
            return None
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                logger.debug(f"Valhalla 400 (unsupported params): {e}")
            else:
                logger.warning(f"Valhalla route error: {e}")
            return None
        except Exception as e:
            logger.warning(f"Valhalla route error: {e}")
            return None

    def alternatives(self, origin: Tuple[float, float], destination: Tuple[float, float],
                     costing: Costing = Costing.AUTO, n: int = 5,
                     avoid: Optional[List[Tuple[float, float]]] = None,
                     congestion_zones: Optional[List[CongestionZone]] = None,
                     traffic_stream: Optional[str] = None) -> List[Route]:
        routes = []

        def _fingerprint(r: Route, n_samples: int = 20) -> str:
            pts = [(p.lat, p.lon) for p in r.shape]
            if not pts:
                return ""
            step = max(1, len(pts) // n_samples)
            samples = [pts[i] for i in range(0, len(pts), step)][:n_samples]
            return json.dumps(samples, sort_keys=True)

        def _add(r: Optional[Route], route_id: str, mode: str = "auto") -> bool:
            if r is None:
                return False
            fp = _fingerprint(r, n_samples=20)
            if any(_fingerprint(existing, n_samples=20) == fp for existing in routes):
                return False
            r.route_id = route_id
            r.mode = mode
            routes.append(r)
            return True

        # 1. Main fastest route (always works)
        _add(self.route(origin, destination, costing, avoid, traffic_stream), "route_fastest")

        # 2. Avoid congestion zones via avoid_locations
        if congestion_zones:
            cz_avoid = [(lat, lon) for lat, lon, _, _ in congestion_zones]
            _add(self.route(origin, destination, costing, avoid=cz_avoid,
                            traffic_stream=traffic_stream), "route_avoid_cz")

        # 3. Multiple avoid-point routes near the midpoint (force different corridors)
        cz_avoid = [(lat, lon) for lat, lon, _, _ in congestion_zones] if congestion_zones else None
        for i in range(n * 2):
            if len(routes) >= n:
                break
            spread_lat = 0.012 * (i - n // 2 + (0.5 if i % 2 else 0))
            spread_lon = 0.012 * (i - n // 2 + (1.0 if i % 2 else 0.5))
            mid_lat = (origin[0] + destination[0]) / 2 + spread_lat
            mid_lon = (origin[1] + destination[1]) / 2 + spread_lon
            avoid_pts = [(mid_lat, mid_lon)]
            if cz_avoid:
                avoid_pts.extend(cz_avoid)
            _add(self.route(origin, destination, costing, avoid=avoid_pts,
                            traffic_stream=traffic_stream), f"route_avoid_{i}")

        # 4. Synthetic fallback only if Valhalla returned nothing
        if not routes:
            routes = self._fallback(origin, destination, n, congestion_zones)

        # 5. Fill remaining slots with synthetic variants from real routes
        while len(routes) < n and routes:
            base = routes[len(routes) % len(routes)]
            f = 0.85 + (len(routes) * 0.1)
            logger.debug(f"Generating synthetic alternative #{len(routes)+1} from {base.route_id}")
            syn = Route(
                route_id=f"route_syn_{len(routes)+1}",
                mode=base.mode,
                duration_seconds=base.duration_seconds * f,
                length_km=base.length_km * f,
                shape=base.shape,
                congestion_level=("heavy" if len(routes) % 2 == 0 else "moderate"),
                has_roadblocks=False,
                has_traffic_delay=(len(routes) > 2),
                description=f"Synthetic variant of {base.route_id}: {base.length_km * f:.1f} km, {base.duration_seconds * f / 60:.0f} min",
                maneuvers=base.maneuvers,
            )
            routes.append(syn)

        # Ensure all routes have clean IDs
        for i, r in enumerate(routes):
            if not r.route_id or r.route_id == "main":
                r.route_id = f"route_{i + 1}"
        return routes[:n]

    def trace_attributes(self, shape: List[Tuple[float, float]], costing: Costing = Costing.AUTO) -> Optional[List[Dict]]:
        try:
            resp = requests.post(f"{self.base_url}/trace_attributes", json={
                "shape": [{"lat": lat, "lon": lon} for lat, lon in shape],
                "costing": costing.value, "shape_match": "map_snap",
                "search_radius": 50,
                "filters": {"attributes": ["edge.way_id", "edge.id"], "action": "include"},
            }, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json().get("edges", [])
        except Exception as e:
            logger.warning(f"trace_attributes failed: {e}")
            return None

    def health(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/status", timeout=5).status_code == 200
        except Exception:
            return False

    def _parse(self, data: Dict, costing: Costing,
               congestion_zones: Optional[List[CongestionZone]] = None) -> Optional[Route]:
        legs = data.get("trip", {}).get("legs", [])
        if not legs:
            return None
        leg = legs[0]
        raw = decode_polyline6(leg.get("shape", ""))
        edge_attrs = leg.get("edge_attributes", [])
        points = []
        for i, (lat, lon) in enumerate(raw):
            attrs = edge_attrs[i] if i < len(edge_attrs) else {}
            edge_id = attrs.get("edge_id", 0)
            speed = attrs.get("speed", 0.0)
            points.append(RoutePoint(lat=lat, lon=lon, edge_id=edge_id, speed=speed))
        s = leg.get("summary", {})
        dur, length = s.get("time", 0), s.get("length", 0)
        speed_kmh = (length / (dur / 3600)) if dur > 0 else 0
        congestion = "light" if speed_kmh > 80 else "moderate" if speed_kmh > 50 else "heavy" if speed_kmh > 25 else "severe"

        if congestion_zones:
            n_nearby = sum(
                1 for lat, lon, _, _ in congestion_zones
                if any(_point_near_zone(p.lat, p.lon, lat, lon, 1000) for p in points)
            )
            if n_nearby >= 3:
                congestion = "severe"
            elif n_nearby >= 2:
                congestion = "heavy"
            elif n_nearby >= 1:
                congestion = "moderate" if congestion == "light" else congestion

        maneuvers = leg.get("maneuvers", [])
        roads = list(dict.fromkeys(sn for m in maneuvers[:5] for sn in m.get("street_names", [])))[:3]
        road_desc = ", ".join(roads) if roads else "local streets"
        parts = [f"Via {road_desc}"]
        if s.get("has_highway"): parts.append("includes highway")
        if s.get("has_toll"): parts.append("has tolls")

        return Route(
            route_id="main", mode=costing.value,
            duration_seconds=dur, length_km=length,
            shape=points, congestion_level=congestion,
            has_roadblocks=False, has_traffic_delay=congestion in ("heavy", "severe"),
            description=". ".join(parts), maneuvers=maneuvers,
        )

    def _fallback(self, origin: Tuple[float, float], destination: Tuple[float, float], n: int,
                  congestion_zones: Optional[List[CongestionZone]] = None) -> List[Route]:
        logger.info("Generating synthetic routes (Valhalla unavailable)")
        base_dur, base_len = 1800, 15.0

        if congestion_zones:
            total_agents = sum(n_ag for _, _, _, n_ag in congestion_zones)
            congestion_mult = 1.0 + min(1.0, total_agents * 0.05)
        else:
            congestion_mult = 1.0

        routes = []
        for i in range(n):
            f = 0.8 + i * 0.3
            dur = base_dur * f * congestion_mult
            near_cz = 0
            if congestion_zones:
                mid_lat = (origin[0] + destination[0]) / 2
                mid_lon = (origin[1] + destination[1]) / 2
                for cz_lat, cz_lon, cz_r, _ in congestion_zones:
                    dist = math.sqrt((mid_lat - cz_lat) ** 2 + (mid_lon - cz_lon) ** 2) * 111_000
                    if dist < cz_r + 2000:
                        near_cz += 1
            if near_cz > 0:
                cl = "heavy" if near_cz > 2 else "moderate"
            else:
                cl = "moderate"
            routes.append(Route(
                route_id=f"route_{i + 1}", mode="auto",
                duration_seconds=dur, length_km=base_len * f,
                shape=[RoutePoint(lat=origin[0], lon=origin[1]), RoutePoint(lat=destination[0], lon=destination[1])],
                congestion_level=cl,
                has_roadblocks=(i == 1), has_traffic_delay=(cl in ("heavy", "severe")),
                description=f"Alternative {i + 1}: {base_len * f:.1f} km, {dur / 60:.0f} min, {cl} traffic",
            ))
        return routes


def _point_near_zone(lat: float, lon: float, cz_lat: float, cz_lon: float, threshold_m: float) -> bool:
    dist = math.sqrt((lat - cz_lat) ** 2 + (lon - cz_lon) ** 2) * 111_000
    return dist < threshold_m
