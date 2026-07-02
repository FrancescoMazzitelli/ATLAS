import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TickRecord:
    tick: int
    timestamp: str
    agents: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class AgentDecision:
    tick: int
    status: str
    position: Tuple[float, float]
    destination: Optional[Tuple[float, float]]
    chosen_route_id: Optional[str]
    alternatives_considered: int
    disruption_encountered: bool
    congestion_zones_active: int
    reasoning: str


@dataclass
class AgentRecord:
    agent_id: str
    profile: Dict[str, Any]
    bio: str
    home: Dict[str, Any]
    work: Optional[Dict[str, Any]]
    route_edges_used: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    final_status: Optional[str] = None
    total_steps: int = 0
    total_distance_km: float = 0.0
    journey: Optional[Dict[str, Any]] = None


class SimulationRecorder:
    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        self.ticks: List[TickRecord] = []
        self.agents: Dict[str, AgentRecord] = {}
        os.makedirs(output_dir, exist_ok=True)

        self._tick_logger = logging.getLogger(f"{__name__}.tick")
        handler = logging.FileHandler(os.path.join(output_dir, "ticks.log"), mode="w")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._tick_logger.handlers = []
        self._tick_logger.addHandler(handler)
        self._tick_logger.setLevel(logging.INFO)
        self._tick_logger.propagate = False

    def register_agent(
        self,
        agent_id: str,
        profile: Any,
        bio: str,
        home: Any,
        work: Optional[Any] = None,
    ):
        def _loc_dict(loc):
            return {"name": loc.name, "lat": loc.lat, "lon": loc.lng, "type": loc.location_type}

        def _profile_dict(p):
            return {
                "age": p.age,
                "sex": p.sex,
                "race": p.race,
                "income": p.income,
                "occupation": p.occupation,
                "has_vehicle": p.has_vehicle,
                "has_transit_pass": p.has_transit_pass,
                "risk_tolerance": p.risk_tolerance,
                "mobility_constraints": list(p.mobility_constraints),
            }

        self.agents[agent_id] = AgentRecord(
            agent_id=agent_id,
            bio=bio,
            profile=_profile_dict(profile),
            home=_loc_dict(home),
            work=_loc_dict(work) if work else None,
        )

    def record_tick(
        self,
        tick: int,
        timestamp: str,
        agent_snapshots: List[Any],
        traffic_edges: List[Dict[str, Any]],
        congestion_zones: List[Dict[str, Any]],
        traffic_snapshot: Any = None,
    ):
        record = TickRecord(tick=tick, timestamp=timestamp)
        for snap in agent_snapshots:
            state = {
                "position": list(snap.position) if snap.position else None,
                "destination": list(snap.destination) if snap.destination else None,
                "is_traveling": snap.is_traveling,
                "arrived": snap.is_done,
                "speed_kph": round(snap.speed_kph, 1),
                "current_edge_id": snap.current_edge_id,
                "progress_pct": round(snap.progress_pct, 1),
                "schedule_status": snap.schedule_status if hasattr(snap, 'schedule_status') else {"type": "unknown"},
            }
            record.agents[snap.agent_id] = state

        tick_data = {
            "tick": tick, "timestamp": timestamp,
            "agents": record.agents,
            "traffic_edges": len(traffic_edges),
            "congestion_zones": len(congestion_zones),
        }
        if traffic_snapshot:
            tick_data["traffic_metrics"] = {
                "total_vehicles": traffic_snapshot.total_vehicles,
                "mean_speed_kph": round(traffic_snapshot.mean_speed_kph, 1),
                "mean_density": round(traffic_snapshot.mean_density_veh_per_km, 2),
                "los_summary": traffic_snapshot.los_summary,
            }
        self._tick_logger.info(json.dumps(tick_data))
        self.ticks.append(record)

    def record_decision(
        self,
        agent_id: str,
        tick: int,
        decision: Dict[str, Any],
        route_edges: List[Dict[str, Any]],
        reasoning: str,
    ):
        if agent_id not in self.agents:
            return

        record = {
            "tick": tick,
            "status": decision.get("status", ""),
            "position": decision.get("current_position"),
            "chosen_route_id": decision.get("chosen_segment") or decision.get("chosen_route"),
            "alternatives_considered": len(decision.get("current_alternatives", [])),
            "disruption_encountered": decision.get("disruption_encountered", False),
            "congestion_zones_active": len(decision.get("congestion_zones", [])),
            "reasoning": reasoning,
            "route_edges": route_edges,
        }
        self.agents[agent_id].decisions.append(record)
        for e in route_edges:
            self.agents[agent_id].route_edges_used.append(e)

    def finalize_agent(self, agent_id: str, status: str, steps: int, distance_km: float = 0.0,
                       journey: Optional[Dict[str, Any]] = None):
        if agent_id in self.agents:
            self.agents[agent_id].final_status = status
            self.agents[agent_id].total_steps = steps
            self.agents[agent_id].total_distance_km = distance_km
            if journey:
                self.agents[agent_id].journey = {
                    "gold_shape": journey.get("gold_shape", []),
                    "gold_maneuvers": journey.get("gold_maneuvers", []),
                    "steps": journey.get("steps", []),
                    "status": journey.get("status"),
                    "agent_id": journey.get("agent_id"),
                    "log": journey.get("log", []),
                }

    def save(self, name: str = "simulation"):
        path = os.path.join(self.output_dir, f"{name}.json")
        data = {
            "summary": {
                "n_agents": len(self.agents),
                "n_ticks": len(self.ticks),
                "n_arrived": sum(1 for a in self.agents.values() if a.final_status == "arrived"),
            },
            "agents": {aid: asdict(rec) for aid, rec in self.agents.items()},
            "ticks": [asdict(t) for t in self.ticks],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Saved simulation record to {path} ({len(self.ticks)} ticks, {len(self.agents)} agents)")
        return path

    def save_ticks_csv(self, name: str = "ticks"):
        import csv
        path = os.path.join(self.output_dir, f"{name}.csv")
        rows = []
        for tick in self.ticks:
            for aid, state in tick.agents.items():
                rows.append({
                    "tick": tick.tick,
                    "timestamp": tick.timestamp,
                    "agent_id": aid,
                    "lat": state.get("position", [None, None])[0] if state.get("position") else None,
                    "lon": state.get("position", [None, None])[1] if state.get("position") else None,
                    "is_traveling": state.get("is_traveling"),
                    "arrived": state.get("arrived"),
                    "speed_kph": state.get("speed_kph"),
                    "current_edge_id": state.get("current_edge_id"),
                    "progress_pct": state.get("progress_pct"),
                })
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["tick", "timestamp", "agent_id", "lat", "lon",
                                                    "is_traveling", "arrived", "speed_kph",
                                                    "current_edge_id", "progress_pct"])
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"Saved tick CSV to {path} ({len(rows)} rows)")
        return path
