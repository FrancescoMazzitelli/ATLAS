import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

from langchain_ollama import ChatOllama

from agentic.core.models import Location, SociodemographicProfile
from agentic.journey.graph import run
from agentic.journey.gold_path import GoldPath, GoldPathGenerator
from routing.engine import ValhallaEngine
from disruption.loader import load_disruptions_from_paths
from simulation.clock import SimulationClock
from simulation.activity import ActivitySchedule
from simulation.traffic import TrafficManager, CongestionTracker
from simulation.recorder import SimulationRecorder, TickRecord

logger = logging.getLogger(__name__)


@dataclass
class SimResult:
    agent_id: str
    journey: Dict
    log: List[str] = field(default_factory=list)


@dataclass
class AgentState:
    agent_id: str
    profile: SociodemographicProfile
    bio: str
    home: Location
    work: Optional[Location] = None
    personality_self_intro: str = ""
    personality_travel_plans: str = ""
    personality_context: list = field(default_factory=list)
    schedule: Optional[ActivitySchedule] = None
    position: Optional[Tuple[float, float]] = None
    destination: Optional[Tuple[float, float]] = None
    gold_waypoints: List[Tuple[float, float]] = field(default_factory=list)
    gold_length_km: float = 0.0
    gold_duration_sec: float = 0.0
    is_traveling: bool = False
    completed_journeys: List[Dict] = field(default_factory=list)
    current_trip_index: int = -1
    is_done: bool = False


class SimulationEngine:
    def __init__(self, valhalla_host: str = "localhost", valhalla_port: int = 8002,
                 valhalla_timeout: int = 30,
                 llm_model: str = "llama3.2", llm_temperature: float = 0.5,
                 log_level: str = "INFO", recursion_limit: int = 50,
                 disruption_files: Optional[List[str]] = None,
                 recorder: Optional[SimulationRecorder] = None):
        logging.basicConfig(level=getattr(logging, log_level.upper()))
        self.valhalla = ValhallaEngine(host=valhalla_host, port=valhalla_port, timeout=valhalla_timeout)
        self.gold_gen = GoldPathGenerator(self.valhalla)
        self.llm = ChatOllama(model=llm_model, temperature=llm_temperature)
        self.recursion_limit = recursion_limit
        self.results: Dict[str, SimResult] = {}
        self.disruption_files = disruption_files or []
        self.recorder = recorder

    @property
    def disruptions(self) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in load_disruptions_from_paths(self.disruption_files)]

    def _log_decision(self, agent_id: str, tick: int, journey: Dict):
        if not self.recorder:
            return
        route_edges = journey.get("route_edges", [])
        reasoning = ""
        log_entries = journey.get("log", [])
        if log_entries:
            reasoning = log_entries[-1].get("content", "") if isinstance(log_entries[-1], dict) else str(log_entries[-1])
        self.recorder.record_decision(
            agent_id, tick,
            decision=journey,
            route_edges=route_edges,
            reasoning=reasoning,
        )

    def _combine_journeys(self, journeys: List[Dict], agent_id: str) -> Dict:
        if not journeys:
            return {"status": "not_started", "step_count": 0, "steps": [], "log": []}
        all_steps = []
        step_offset = 0
        all_logs = []
        for j in journeys:
            steps = j.get("steps", [])
            for s in steps:
                s = dict(s)
                s["index"] = s["index"] + step_offset
                all_steps.append(s)
            step_offset += len(steps)
            all_logs.extend(j.get("log", []))
        last = journeys[-1]
        return {
            "agent_id": agent_id,
            "status": last.get("status", "arrived"),
            "current_lat": last.get("current_lat"),
            "current_lon": last.get("current_lon"),
            "step_count": len(all_steps),
            "steps": all_steps,
            "log": all_logs,
            "gold_shape": last.get("gold_shape", []),
            "gold_maneuvers": last.get("gold_maneuvers", []),
        }

    def run(self,
            agents: List[Tuple[str, SociodemographicProfile, Location, Optional[Location]]],
            clock: SimulationClock,
            bios: Optional[List[str]] = None,
            personalities: Optional[List[Dict]] = None) -> Dict[str, SimResult]:
        self.results = {}
        disruptions = self.disruptions
        tracker = CongestionTracker()
        traffic_mgr = TrafficManager()
        base_date = clock.start_datetime

        agent_states: List[AgentState] = []
        for i, (aid, profile, home, work) in enumerate(agents):
            bio = (bios[i] if bios and i < len(bios) else "") or profile.to_text()
            pers = (personalities[i] if personalities and i < len(personalities) else {})
            profile.home_location = home
            profile.work_location = work
            schedule = ActivitySchedule.from_profile(aid, base_date, profile)
            dest = (work.lat, work.lng) if work else (home.lat + 0.05, home.lng + 0.03)
            gold = self.gold_gen.generate((home.lat, home.lng), dest)
            as_ = AgentState(
                agent_id=aid, profile=profile, bio=bio, home=home, work=work,
                schedule=schedule,
                personality_self_intro=pers.get("self_intro", bio),
                personality_travel_plans=pers.get("travel_plans", ""),
                personality_context=pers.get("context", []),
                position=(home.lat, home.lng),
                destination=dest,
                gold_waypoints=gold.waypoints,
                gold_length_km=gold.total_length_km,
                gold_duration_sec=gold.total_duration_sec,
            )
            agent_states.append(as_)
            tracker.add_agent(aid, home.lat, home.lng)
            if self.recorder:
                self.recorder.register_agent(aid, profile, bio, home, work)

        logger.info(f"Starting tick-based simulation: {len(agent_states)} agents, "
                    f"{clock.max_ticks} ticks from {clock.start_datetime}")

        while not clock.is_done():
            clock.advance()
            dt = clock.current_datetime()
            tick = clock.current_tick
            logger.debug(f"Tick {tick}: {dt.isoformat()}")

            traffic_stream = traffic_mgr.build_traffic_stream()
            if traffic_stream:
                logger.debug(f"Tick {tick}: {traffic_mgr.edge_count} edges with traffic")

            for as_ in agent_states:
                if as_.is_done:
                    continue

                status = as_.schedule.status_at(dt) if as_.schedule else {"type": "idle"}

                # Start a new trip when the schedule says so
                if status["type"] == "traveling" and not as_.is_traveling:
                    for i, trip in enumerate(as_.schedule.trips):
                        if trip.contains(dt) and i > as_.current_trip_index:
                            as_.current_trip_index = i
                            as_.is_traveling = True
                            as_.position = (trip.origin.lat, trip.origin.lng)
                            as_.destination = (trip.destination.lat, trip.destination.lng)
                            gold = self.gold_gen.generate(as_.position, as_.destination)
                            as_.gold_waypoints = gold.waypoints
                            as_.gold_length_km = gold.total_length_km
                            as_.gold_duration_sec = gold.total_duration_sec
                            logger.info(f"{as_.agent_id} started trip {i}: {status.get('purpose', 'unknown')}")
                            break

                if as_.is_traveling:
                    cz = tracker.zones
                    journey = run(
                        agent_id=as_.agent_id,
                        profile_text=as_.bio,
                        origin=as_.position,
                        destination=as_.destination,
                        gold_waypoints=as_.gold_waypoints,
                        gold_length_km=as_.gold_length_km,
                        gold_duration_sec=as_.gold_duration_sec,
                        valhalla=self.valhalla,
                        llm=self.llm,
                        recursion_limit=self.recursion_limit,
                        disruptions=disruptions,
                        congestion_zones=cz,
                        traffic_stream=traffic_stream or None,
                        personality_self_intro=as_.personality_self_intro,
                        personality_travel_plans=as_.personality_travel_plans,
                        personality_context=as_.personality_context,
                    )
                    self._log_decision(as_.agent_id, tick, journey)

                    if journey.get("status") == "arrived":
                        as_.position = (journey["current_lat"], journey["current_lon"])
                        as_.is_traveling = False
                        tracker.add_agent(as_.agent_id, as_.position[0], as_.position[1])
                        traffic_mgr.remove_agent(as_.agent_id)
                        as_.completed_journeys.append(journey)
                        logger.info(f"{as_.agent_id} arrived at destination")

                        # Check if all trips are done
                        if as_.current_trip_index >= len(as_.schedule.trips) - 1:
                            as_.is_done = True
                            if self.recorder:
                                combined = self._combine_journeys(as_.completed_journeys, as_.agent_id)
                                self.recorder.finalize_agent(
                                    as_.agent_id, "arrived",
                                    combined.get("step_count", 0), 0,
                                    journey=combined,
                                )

                        # Create synthetic ticks for this leg so traffic map shows movement
                        if self.recorder:
                            steps = journey.get("steps", [])
                            for step_i, step in enumerate(steps):
                                frm = step.get("from")
                                if frm and len(frm) >= 2 and frm[0] is not None:
                                    syn_tick = tick + step_i + 1
                                    rec = TickRecord(tick=syn_tick, timestamp=dt.isoformat())
                                    rec.agents[as_.agent_id] = {
                                        "position": list(frm),
                                        "destination": list(as_.destination) if as_.destination else None,
                                        "is_traveling": True,
                                        "arrived": step_i == len(steps) - 1,
                                        "step_count": step["index"] + 1,
                                        "route_edge_count": 0,
                                    }
                                    self.recorder.ticks.append(rec)
                    else:
                        last_step = journey["steps"][-1] if journey.get("steps") else None
                        if last_step:
                            as_.position = tuple(last_step["to"])
                            tracker.add_agent(as_.agent_id, as_.position[0], as_.position[1])
                        journey_edges = journey.get("route_edges", [])
                        for edge_info in journey_edges:
                            traffic_mgr.record_edge(
                                as_.agent_id, edge_info["edge_id"],
                                edge_info.get("speed", 50),
                                edge_info.get("length_m", 100),
                            )

            tracker.update({as_.agent_id: as_.position for as_ in agent_states if as_.position})

            if self.recorder:
                edges_snapshot = traffic_mgr.edge_summary()
                self.recorder.record_tick(tick, dt.isoformat(), agent_states, edges_snapshot, tracker.zones)

        for as_ in agent_states:
            combined = self._combine_journeys(as_.completed_journeys, as_.agent_id)
            result = SimResult(
                agent_id=as_.agent_id,
                journey=combined,
            )
            self.results[as_.agent_id] = result

        n_arrived = sum(1 for r in self.results.values() if r.journey.get("status") == "arrived")
        logger.info(f"Simulation complete: {n_arrived}/{len(agent_states)} agents arrived")
        return self.results

    def run_agent(self, agent_id: str, profile: SociodemographicProfile,
                  home: Location, work: Optional[Location] = None,
                  congestion_zones: Optional[List[Dict[str, Any]]] = None,
                  traffic_stream: Optional[str] = None,
                  personality_self_intro: str = "",
                  personality_travel_plans: str = "",
                  personality_context: Optional[List[Tuple[str, str]]] = None) -> SimResult:
        profile.home_location, profile.work_location = home, work
        dest = (work.lat, work.lng) if work else (home.lat + 0.05, home.lng + 0.03)

        gold = self.gold_gen.generate((home.lat, home.lng), dest)
        cz = congestion_zones or []

        journey = run(
            agent_id=agent_id,
            profile_text=profile.to_text(),
            origin=(home.lat, home.lng),
            destination=dest,
            gold_waypoints=gold.waypoints,
            gold_length_km=gold.total_length_km,
            gold_duration_sec=gold.total_duration_sec,
            valhalla=self.valhalla,
            llm=self.llm,
            recursion_limit=self.recursion_limit,
            disruptions=self.disruptions,
            congestion_zones=cz,
            traffic_stream=traffic_stream,
            personality_self_intro=personality_self_intro,
            personality_travel_plans=personality_travel_plans,
            personality_context=personality_context,
        )

        result = SimResult(agent_id=agent_id, journey=journey)
        self.results[agent_id] = result
        return result

    def summary(self) -> Dict:
        if not self.results:
            return {"status": "no_results"}
        return {"n_agents": len(self.results),
                "per_agent": {aid: {"status": r.journey.get("status"), "steps": r.journey.get("step_count")}
                              for aid, r in self.results.items()}}
