import logging
import random
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

from langchain_ollama import ChatOllama

from agentic.core.models import Location, SociodemographicProfile
from agentic.journey.graph import run
from agentic.journey.gold_path import GoldPath, GoldPathGenerator
from agentic.decisions.discretionary import ask_discretionary
from routing.engine import ValhallaEngine
from disruption.loader import load_disruptions_from_paths
from disruption.scenarios import Disruption
from simulation.clock import SimulationClock
from simulation.activity import ActivitySchedule, Trip
from simulation.traffic import TrafficManager, CongestionTracker, RouteInterpolator, TrafficMetricsSnapshot
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

    itinerary: List[Tuple[str, Location, str]] = field(default_factory=list)
    itinerary_idx: int = 0
    discretionary_skipped: List[int] = field(default_factory=list)
    discretionary_decisions: List[Dict] = field(default_factory=list)

    current_path: Optional[RouteInterpolator] = None
    path_elapsed_sec: float = 0.0
    awaiting_decision: bool = False
    current_trip_purpose: str = ""


@dataclass
class AgentSnapshot:
    agent_id: str
    position: Optional[Tuple[float, float]]
    destination: Optional[Tuple[float, float]]
    is_traveling: bool
    is_done: bool
    speed_kph: float
    current_edge_id: int
    progress_pct: float
    schedule_status: dict


class SimulationEngine:
    def __init__(self, valhalla_host: str = "localhost", valhalla_port: int = 8002,
                 valhalla_timeout: int = 30,
                 llm_model: str = "llama3.2", llm_temperature: float = 0.5,
                 llm_num_predict: int = 4096,
                 log_level: str = "INFO", recursion_limit: int = 50,
                 disruption_files: Optional[List[str]] = None,
                 recorder: Optional[SimulationRecorder] = None,
                 seed: Optional[int] = None,
                 discretionary_enabled: bool = True,
                 discretionary_social_invitation: str = "",
                 docker_container: str = "",
                 valhalla_config: str = "/etc/valhalla/valhalla.json",
                 container_traffic_dir: str = "/traffic",
                 traffic_backup_dir: str = "traffic_backup",
                 jam_density_per_km: float = 50.0):
        logging.basicConfig(level=getattr(logging, log_level.upper()))
        self.valhalla = ValhallaEngine(host=valhalla_host, port=valhalla_port, timeout=valhalla_timeout)
        self.gold_gen = GoldPathGenerator(self.valhalla)
        self.llm = ChatOllama(model=llm_model, temperature=llm_temperature, num_predict=llm_num_predict)
        self.recursion_limit = recursion_limit
        self.results: Dict[str, SimResult] = {}
        self.disruption_files = disruption_files or []
        self.recorder = recorder
        self.random = random.Random(seed) if seed is not None else random
        self.discretionary_enabled = discretionary_enabled
        self.discretionary_social_invitation = discretionary_social_invitation
        self.llm_num_predict = llm_num_predict

        self.traffic_mgr = TrafficManager(
            jam_density_per_km=jam_density_per_km,
            docker_container=docker_container,
            valhalla_config=valhalla_config,
            container_traffic_dir=container_traffic_dir,
            traffic_backup_dir=traffic_backup_dir,
        )
        self.tracker = CongestionTracker()
        self.metrics_snapshots: List[TrafficMetricsSnapshot] = []
        self._agent_states: List[AgentState] = []
        self._all_disruptions: List[Disruption] = load_disruptions_from_paths(self.disruption_files)

    def _active_disruptions(self, dt: datetime) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self._all_disruptions if d.is_active_at(dt)]

    def _start_trip(self, as_: AgentState, origin: Tuple[float, float],
                    destination: Tuple[float, float], trip_purpose: str,
                    clock: SimulationClock):
        as_.position = origin
        as_.destination = destination
        as_.is_traveling = True
        as_.awaiting_decision = True
        as_.current_trip_purpose = trip_purpose
        as_.path_elapsed_sec = 0.0
        as_.current_path = None

        gold = self.gold_gen.generate(origin, destination)
        as_.gold_waypoints = gold.waypoints
        as_.gold_length_km = gold.total_length_km
        as_.gold_duration_sec = gold.total_duration_sec

        logger.info(f"{as_.agent_id} started trip: {trip_purpose} "
                    f"({origin} -> {destination}) at tick {clock.current_tick}")

    def _make_route_decision(self, as_: AgentState,
                              dt: datetime,
                              clock: SimulationClock) -> bool:
        cz = self.tracker.zones
        traffic_stream = self.traffic_mgr.traffic_stream
        disruptions = self._active_disruptions(dt)

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
            llm_num_predict=self.llm_num_predict,
            disruptions=disruptions,
            congestion_zones=cz,
            traffic_stream=traffic_stream or None,
            personality_self_intro=as_.personality_self_intro,
            personality_travel_plans=as_.personality_travel_plans,
            personality_context=as_.personality_context,
        )

        steps = journey.get("steps", [])
        if not steps:
            logger.warning(f"{as_.agent_id}: no steps returned from journey")
            return False

        last_step = steps[-1]
        path_coords = last_step.get("path_coords", [])

        if not path_coords:
            logger.warning(f"{as_.agent_id}: no path_coords in journey step")
            return False

        route_edges = last_step.get("route_edges", []) or journey.get("route_edges", [])
        for edge_info in route_edges:
            self.traffic_mgr.record_edge(
                as_.agent_id, edge_info["edge_id"],
                edge_info.get("speed", 50),
                edge_info.get("length_m", 100),
            )

        route_shape_pts = [(p[0], p[1]) for p in path_coords]
        if len(route_shape_pts) < 2:
            logger.warning(f"{as_.agent_id}: path too short ({len(route_shape_pts)} pts)")
            return False

        min_route = self.valhalla.route(as_.position, last_step.get("to", as_.destination))
        if min_route:
            as_.current_path = RouteInterpolator.from_route(min_route)
        else:
            from routing.engine import Route, RoutePoint
            dummy = Route(
                route_id="dummy", mode="auto",
                duration_seconds=60, length_km=1.0,
                shape=[RoutePoint(lat=p[0], lon=p[1]) for p in route_shape_pts],
                congestion_level="light", has_roadblocks=False,
                has_traffic_delay=False, description="",
            )
            as_.current_path = RouteInterpolator.from_route(dummy)

        as_.path_elapsed_sec = 0.0
        as_.awaiting_decision = False

        if self.recorder:
            reasoning = ""
            log_entries = journey.get("log", [])
            if log_entries:
                reasoning = str(log_entries[-1])[:500]
            self.recorder.record_decision(
                as_.agent_id, clock.current_tick,
                decision=journey, route_edges=route_edges,
                reasoning=reasoning,
            )

        if journey.get("status") == "arrived":
            as_.is_traveling = False
            as_.current_path = None
            as_.completed_journeys.append(journey)
            self.traffic_mgr.remove_agent(as_.agent_id)
            logger.info(f"{as_.agent_id} arrived at destination")
            return True

        return False

    def run(self,
            agents: List[Tuple[str, SociodemographicProfile, Location, Optional[Location]]],
            clock: SimulationClock,
            bios: Optional[List[str]] = None,
            personalities: Optional[List[Dict]] = None,
            itineraries: Optional[List[List[Tuple[str, Location, str]]]] = None,
            departure_times_list: Optional[List[List[str]]] = None) -> Dict[str, SimResult]:
        self.results = {}
        self.metrics_snapshots = []
        base_date = clock.start_datetime

        self.traffic_mgr.backup_traffic()

        self._agent_states = []
        for i, (aid, profile, home, work) in enumerate(agents):
            bio = (bios[i] if bios and i < len(bios) else "") or profile.to_text()
            pers = (personalities[i] if personalities and i < len(personalities) else {})
            profile.home_location = home
            profile.work_location = work
            itin = (itineraries[i] if itineraries and i < len(itineraries) else None)

            if itin and len(itin) >= 2:
                deps = (departure_times_list[i] if departure_times_list and i < len(departure_times_list) else [])
                schedule = ActivitySchedule.from_itinerary(aid, base_date, itin, deps, profile)
                first_dest = itin[1][1]
                dest = (first_dest.lat, first_dest.lng)
            else:
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
                itinerary=itin or [],
            )
            self._agent_states.append(as_)
            self.tracker.add_agent(aid, home.lat, home.lng)
            if self.recorder:
                self.recorder.register_agent(aid, profile, bio, home, work)

        logger.info(f"Starting tick-based simulation: {len(self._agent_states)} agents, "
                    f"{clock.max_ticks} ticks (1s each) from {clock.start_datetime}")

        while not clock.is_done():
            clock.advance()
            dt = clock.current_datetime()
            tick = clock.current_tick

            self.traffic_mgr.build_traffic_stream()

            for as_ in self._agent_states:
                if as_.is_done:
                    continue

                status = as_.schedule.status_at(dt) if as_.schedule else {"type": "idle"}

                if status["type"] == "traveling" and not as_.is_traveling and not as_.awaiting_decision:
                    for i, trip in enumerate(as_.schedule.trips):
                        if trip.contains(dt) and i > as_.current_trip_index:
                            as_.current_trip_index = i
                            self._start_trip(as_, (trip.origin.lat, trip.origin.lng),
                                             (trip.destination.lat, trip.destination.lng),
                                             trip.purpose, clock)

                            if trip.purpose == "discretionary" and self.discretionary_enabled:
                                self._make_discretionary_decision(as_)
                                if as_.current_trip_index in as_.discretionary_skipped:
                                    home_loc = as_.profile.home_location or trip.origin
                                    as_.schedule.trips[i] = Trip(
                                        origin=trip.origin, destination=home_loc,
                                        departure=trip.departure, arrival=trip.arrival,
                                        mode=trip.mode, purpose="commute_to_home",
                                        distance_km=trip.distance_km,
                                    )
                                    self._start_trip(as_, (trip.origin.lat, trip.origin.lng),
                                                     (home_loc.lat, home_loc.lng),
                                                     "commute_to_home", clock)
                            break

                if as_.awaiting_decision:
                    arrived = self._make_route_decision(as_, dt, clock)
                    if arrived:
                        if as_.current_trip_index >= len(as_.schedule.trips) - 1:
                            as_.is_done = True
                            if self.recorder:
                                combined = self._combine_journeys(as_.completed_journeys, as_.agent_id)
                                self.recorder.finalize_agent(
                                    as_.agent_id, "arrived",
                                    combined.get("step_count", 0), 0, journey=combined,
                                )

            edge_densities = self.traffic_mgr.edge_density_map()

            for as_ in self._agent_states:
                if as_.is_done or not as_.is_traveling or as_.awaiting_decision:
                    continue

                as_.path_elapsed_sec += 1.0

                pos = as_.current_path.position_at(
                    as_.path_elapsed_sec,
                    jam_density=self.traffic_mgr.jam_density,
                    edge_densities=edge_densities,
                )
                as_.position = pos

                current_edge = as_.current_path.edge_at(
                    as_.path_elapsed_sec,
                    jam_density=self.traffic_mgr.jam_density,
                    edge_densities=edge_densities,
                )
                self.tracker.add_agent(as_.agent_id, pos[0], pos[1])

                if as_.path_elapsed_sec >= as_.current_path.total_duration_sec:
                    as_.awaiting_decision = True

            self.tracker.update({as_.agent_id: as_.position for as_ in self._agent_states if as_.position})

            snap = self.traffic_mgr.metrics.snapshot(tick, dt.isoformat())
            self.metrics_snapshots.append(snap)

            if self.recorder:
                agent_snaps = []
                for as_ in self._agent_states:
                    speed = 0.0
                    if as_.is_traveling and as_.current_path:
                        edge_id = as_.current_path.edge_at(
                            as_.path_elapsed_sec,
                            jam_density=self.traffic_mgr.jam_density,
                            edge_densities=edge_densities,
                        )
                        density = edge_densities.get(edge_id, 0)
                        ratio = density / max(self.traffic_mgr.jam_density, 1)
                        speed = 50 * max(0.05, 1.0 - ratio)
                    agent_snaps.append(AgentSnapshot(
                        agent_id=as_.agent_id,
                        position=as_.position,
                        destination=as_.destination,
                        is_traveling=as_.is_traveling,
                        is_done=as_.is_done,
                        speed_kph=speed,
                        current_edge_id=edge_id if as_.current_path else 0,
                        progress_pct=as_.current_path.progress_pct(as_.path_elapsed_sec) if as_.current_path else 0,
                        schedule_status=as_.schedule.status_at(dt) if as_.schedule else {"type": "unknown"},
                    ))
                self.recorder.record_tick(
                    tick, dt.isoformat(), agent_snaps,
                    self.traffic_mgr.edge_summary(),
                    self.tracker.zones, snap,
                )

        self.traffic_mgr.save_metrics(self.metrics_snapshots, self.recorder.output_dir if self.recorder else "output")

        for as_ in self._agent_states:
            combined = self._combine_journeys(as_.completed_journeys, as_.agent_id)
            result = SimResult(
                agent_id=as_.agent_id,
                journey=combined,
                log=[f"discretionary_decisions: {as_.discretionary_decisions}"],
            )
            self.results[as_.agent_id] = result

        n_arrived = sum(1 for r in self.results.values() if r.journey.get("status") == "arrived")
        logger.info(f"Simulation complete: {n_arrived}/{len(self._agent_states)} agents arrived")
        return self.results

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

    def _make_discretionary_decision(self, as_: AgentState) -> bool:
        if not self.discretionary_enabled:
            return False
        if as_.current_trip_index >= len(as_.schedule.trips):
            return False
        trip = as_.schedule.trips[as_.current_trip_index]
        disc_context = ""
        for i, (loc_type, loc, ctx) in enumerate(as_.itinerary):
            if loc_type == "DISCRETIONARY" and (
                (loc.lat, loc.lng) == (trip.destination.lat, trip.destination.lng)
                or i == as_.itinerary_idx + 1
            ):
                disc_context = ctx
                break
        if self.discretionary_social_invitation:
            invitation_text = self.discretionary_social_invitation.format(
                context=disc_context or "run a personal errand"
            )
        else:
            invitation_text = f"A colleague invites you to grab a beer. You planned to {disc_context or 'run an errand'}. Do you go?"
        decision = ask_discretionary(
            agent_id=as_.agent_id,
            personality_self_intro=as_.personality_self_intro,
            personality_travel_plans=as_.personality_travel_plans,
            personality_context=as_.personality_context,
            current_activity_context=disc_context,
            discretionary_location_name=trip.destination.name,
            social_invitation_template=invitation_text,
            llm=self.llm,
            random_state=self.random,
        )
        as_.discretionary_decisions.append({
            "trip_index": as_.current_trip_index,
            "decision": "go" if decision else "skip",
            "purpose": trip.purpose,
            "destination": {"name": trip.destination.name, "lat": trip.destination.lat, "lon": trip.destination.lng},
            "context": disc_context,
        })
        if not decision:
            as_.discretionary_skipped.append(as_.current_trip_index)
            home = as_.profile.home_location
            if home:
                as_.destination = (home.lat, home.lng)
                logger.info(f"{as_.agent_id} SKIPPED discretionary trip {as_.current_trip_index}, redirecting HOME")
        return decision

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

    def summary(self) -> Dict:
        if not self.results:
            return {"status": "no_results"}
        n_skipped = sum(
            len(as_.discretionary_skipped)
            for as_ in getattr(self, '_agent_states', [])
        ) if hasattr(self, '_agent_states') else 0
        return {
            "n_agents": len(self.results),
            "n_metrics_snapshots": len(self.metrics_snapshots),
            "per_agent": {
                aid: {
                    "status": r.journey.get("status"),
                    "steps": r.journey.get("step_count"),
                }
                for aid, r in self.results.items()
            },
        }
