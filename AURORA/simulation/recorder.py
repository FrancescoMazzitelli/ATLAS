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
        agent_states: List[Any],
        traffic_edges: List[Dict[str, Any]],
        congestion_zones: List[Dict[str, Any]],
    ):
        record = TickRecord(tick=tick, timestamp=timestamp)
        for as_ in agent_states:
            edge_count = 0
            completed = getattr(as_, "completed_journeys", [])
            if completed:
                edges = completed[-1].get("route_edges", []) if completed else []
                edge_count = len(edges)

            state = {
                "position": list(as_.position) if as_.position else None,
                "destination": list(as_.destination) if as_.destination else None,
                "is_traveling": as_.is_traveling,
                "arrived": as_.is_done,
                "step_count": sum(j.get("step_count", 0) for j in getattr(as_, "completed_journeys", [])),
                "route_edge_count": edge_count,
                "schedule_status": as_.schedule.status_at(datetime.fromisoformat(timestamp)) if as_.schedule else {"type": "unknown"},
            }
            record.agents[as_.agent_id] = state

        tick_data = {"tick": tick, "timestamp": timestamp, "agents": record.agents,
                     "traffic_edges": len(traffic_edges),
                     "congestion_zones": len(congestion_zones)}
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
            "alternatives_considered": len(decision.get("segments", decision.get("alternatives", []))),
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
                # Store only what's needed: gold_shape, gold_maneuvers, steps
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
                    "step_count": state.get("step_count"),
                    "route_edge_count": state.get("route_edge_count"),
                })
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["tick", "timestamp", "agent_id", "lat", "lon",
                                                    "is_traveling", "arrived", "step_count",
                                                    "route_edge_count"])
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"Saved tick CSV to {path} ({len(rows)} rows)")
        return path
