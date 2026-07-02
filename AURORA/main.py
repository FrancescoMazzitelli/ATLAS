import logging
import json
import argparse
import os
import csv
from typing import Optional, Tuple, List, Dict

from agentic.core.models import SociodemographicProfile, Location
from simulation.engine import SimulationEngine
from simulation.recorder import SimulationRecorder
from simulation.clock import SimulationClock
from simulation.config import load_config
from geocoding.nominatim import NominatimClient


CHICAGO_DEFAULT = (41.8781, -87.6298)
CHICAGO_HOME_DEFAULT = (41.88, -87.63)


def _load_csv_coords(csv_path: str) -> Dict[int, List[Dict]]:
    """Load agents.csv and return {agent_idx: [{o_x, o_y, d_x, d_y, departure_sec}, ...]}."""
    coords_by_agent: Dict[int, List[Dict]] = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aidx = int(row["agent_idx"])
            entry = {
                "o_x": float(row["o_x"]),
                "o_y": float(row["o_y"]),
                "d_x": float(row["d_x"]),
                "d_y": float(row["d_y"]),
                "departure_sec": int(row["departure_sec"]),
            }
            coords_by_agent.setdefault(aidx, []).append(entry)
    return coords_by_agent


def _resolve_location_coords(
    entry: dict,
    loc_idx: int,
    loc_type: str,
    csv_legs: List[Dict],
    nominatim: Optional[NominatimClient] = None,
) -> Tuple[float, float, str]:
    """Resolve coordinates for a location in the itinerary.

    CSV rows represent trip legs (L0->L1, L1->L2, ...).
    For loc_idx=0 use CSV leg 0 origin; for loc_idx>0 use CSV leg (loc_idx-1) destination.
    Priority:
    1. JSONL embedded coordinates (itinerary.coordinates)
    2. CSV coordinates (agents.csv) matched by position
    3. Default Chicago coordinates
    """
    itinerary = entry.get("itinerary", {})
    coords = itinerary.get("coordinates")
    if coords and loc_idx < len(coords):
        c = coords[loc_idx]
        lat = float(c.get("lat", c.get("y", CHICAGO_DEFAULT[0])))
        lon = float(c.get("lon", c.get("lng", c.get("x", CHICAGO_DEFAULT[1]))))
        name = None
        if nominatim:
            name = nominatim.reverse_name(lat, lon)
        return lat, lon, name or f"{loc_type.lower()}_{loc_idx}"

    if csv_legs:
        if loc_idx == 0:
            lat, lon = csv_legs[0]["o_y"], csv_legs[0]["o_x"]
            name = None
            if nominatim:
                name = nominatim.reverse_name(lat, lon)
            return lat, lon, name or f"{loc_type.lower()}_{loc_idx}"
        elif loc_idx <= len(csv_legs):
            leg = csv_legs[loc_idx - 1]
            lat, lon = leg["d_y"], leg["d_x"]
            name = None
            if nominatim:
                name = nominatim.reverse_name(lat, lon)
            return lat, lon, name or f"{loc_type.lower()}_{loc_idx}"

    if loc_type == "HOME":
        return (CHICAGO_HOME_DEFAULT[0], CHICAGO_HOME_DEFAULT[1], "Home")
    elif loc_type in ("WORK", "SCHOOL"):
        return (CHICAGO_DEFAULT[0], CHICAGO_DEFAULT[1], "Workplace")

    return (CHICAGO_DEFAULT[0], CHICAGO_DEFAULT[1], "Misc")


def _load_agents_from_jsonl(
    jsonl_path: str,
    csv_path: Optional[str] = None,
    max_agents: Optional[int] = None,
    nominatim: Optional[NominatimClient] = None,
) -> list:
    entries = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if max_agents:
        entries = entries[:max_agents]

    csv_by_agent = {}
    if csv_path and os.path.exists(csv_path):
        csv_by_agent = _load_csv_coords(csv_path)

    agent_list = []
    for entry in entries:
        aid = f"agent_{entry['agent_idx']:04d}"
        bio = entry.get("self_introduction", "")
        locs = entry.get("itinerary", {}).get("locations", [])
        contexts = entry.get("itinerary", {}).get("location_context", [])
        dept_times = entry.get("itinerary", {}).get("departure_times", [])

        if not locs:
            continue

        csv_legs = csv_by_agent.get(entry["agent_idx"], [])

        resolved: List[Tuple[str, float, float, str]] = []
        for i, lt in enumerate(locs):
            lat, lon, name = _resolve_location_coords(entry, i, lt, csv_legs, nominatim)
            resolved.append((lt, lat, lon, name))

        home = Location(f"h_{aid}", resolved[0][3], resolved[0][1], resolved[0][2], "home")
        has_work = "WORK" in locs or "SCHOOL" in locs
        has_disc = "DISCRETIONARY" in locs
        occ = None
        work = None
        if has_work:
            occ = "Education/Legal" if "SCHOOL" in locs else "Management/Business"
            wrk = resolved[locs.index("WORK" if "WORK" in locs else "SCHOOL")]
            work = Location(f"w_{aid}", wrk[3], wrk[1], wrk[2], "work")
        elif has_disc:
            occ = "Sales/Office"
            disc_idx = locs.index("DISCRETIONARY")
            disc = resolved[disc_idx]
            work = Location(f"w_{aid}", disc[3], disc[1], disc[2], "work")

        profile = SociodemographicProfile(
            age=35, sex="Unknown", occupation=occ,
            has_vehicle=True, has_transit_pass=False,
            risk_tolerance=0.5,
        )

        itinerary_locations: List[Tuple[str, Location, str]] = []
        for i, lt in enumerate(resolved):
            loc_type, lat, lon, name = lt
            ctx = contexts[i] if i < len(contexts) else ""
            loc = Location(f"loc_{aid}_{i}", name, lat, lon, loc_type.lower())
            itinerary_locations.append((loc_type, loc, ctx))

        agent_list.append({
            "id": aid,
            "profile": profile,
            "home": home,
            "work": work,
            "bio": bio,
            "itinerary_locations": itinerary_locations,
            "departure_times": dept_times,
            "personality": {
                "self_intro": entry.get("self_introduction", ""),
                "travel_plans": entry.get("travel_plans_summary", ""),
                "context": list(zip(locs, contexts)),
            },
        })

    logging.info(f"Loaded {len(agent_list)} agents from {jsonl_path}")
    return agent_list


def _print_agent_reasoning(journey: dict):
    steps = journey.get("steps", [])
    log = journey.get("log", [])
    for s in steps:
        seg = s.get("segment", "?")
        reasoning = s.get("reasoning", "")
        maneuver = s.get("maneuver", 0)
        frm = s.get("from", [0, 0])
        to = s.get("to", [0, 0])
        print(f"    Step {s['index']}: maneuver {maneuver} | {seg}")
        if reasoning:
            print(f"      Reasoning: {reasoning}")
        print(f"      ({frm[0]:.4f},{frm[1]:.4f}) \u2192 ({to[0]:.4f},{to[1]:.4f})")


def main():
    parser = argparse.ArgumentParser(
        description="AURORA \u2014 Autonomous Urban Reasoning and Optimized Routing Adaptation"
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="config file path")
    parser.add_argument("--list-agents", action="store_true", help="list loaded agents and exit")
    parser.add_argument("-n", "--population", type=int, default=0,
                        help="override number of agents (overrides config)")
    parser.add_argument("--tick-mode", action="store_true",
                        help="run in tick-based multi-agent mode (default: sequential)")
    parser.add_argument("--output-dir", default="output",
                        help="output directory for simulation recordings and maps")
    parser.add_argument("--map", action="store_true",
                        help="generate traffic congestion & agent path maps")
    parser.add_argument("--no-record", action="store_true",
                        help="disable tick-by-tick recording")
    parser.add_argument("--csv-coords", default="data/agents.csv",
                        help="CSV file with origin/destination coordinates per leg")
    args = parser.parse_args()

    cfg = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, cfg.simulation.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    max_n = args.population if args.population > 0 else None

    nominatim = None
    if cfg.nominatim.host:
        nominatim = NominatimClient(
            host=cfg.nominatim.host,
            port=cfg.nominatim.port,
            timeout=cfg.nominatim.timeout,
        )

    agent_list = _load_agents_from_jsonl(
        cfg.data.agents_file or "data/agents.jsonl",
        csv_path=args.csv_coords,
        max_agents=max_n or cfg.data.num_agents,
        nominatim=nominatim,
    )

    if args.list_agents:
        print(f"Loaded {len(agent_list)} agents:\n")
        for a in agent_list:
            locs = [x[0] for x in a["itinerary_locations"]]
            print(f"  {a['id']}: {a['bio'][:60]}...")
            print(f"       Itinerary: {' \u2192 '.join(locs)}")
            print()
        return

    os.makedirs(args.output_dir, exist_ok=True)

    recorder = None
    if not args.no_record:
        recorder = SimulationRecorder(output_dir=args.output_dir)

    engine = SimulationEngine(
        valhalla_host=cfg.valhalla.host,
        valhalla_port=cfg.valhalla.port,
        valhalla_timeout=cfg.valhalla.timeout,
        llm_model=cfg.llm.model,
        llm_temperature=cfg.llm.temperature,
        llm_num_predict=cfg.llm.num_predict,
        log_level=cfg.simulation.log_level,
        recursion_limit=cfg.simulation.recursion_limit,
        disruption_files=list(cfg.simulation.disruption_files),
        recorder=recorder,
        seed=cfg.simulation.seed,
        discretionary_enabled=cfg.discretionary.enabled,
        discretionary_social_invitation=cfg.discretionary.social_invitation,
        docker_container=cfg.traffic.docker_container,
        valhalla_config=cfg.traffic.valhalla_config,
        container_traffic_dir=cfg.traffic.container_traffic_dir,
        traffic_backup_dir=cfg.traffic.traffic_backup_dir,
        jam_density_per_km=cfg.traffic.jam_density_per_km,
    )

    if args.tick_mode:
        clock = SimulationClock(
            start_datetime=__import__("datetime").datetime.fromisoformat(
                cfg.simulation.clock.start_datetime
            ),
            tick_duration_seconds=cfg.simulation.clock.tick_duration_seconds,
            max_ticks=cfg.simulation.clock.max_ticks,
        )
        agent_tuples = [(a["id"], a["profile"], a["home"], a["work"]) for a in agent_list]
        bios = [a["bio"] for a in agent_list]
        personalities = [a["personality"] for a in agent_list]
        itineraries = [a.get("itinerary_locations", []) for a in agent_list]
        departure_times = [a.get("departure_times", []) for a in agent_list]

        results = engine.run(
            agent_tuples, clock,
            bios=bios,
            personalities=personalities,
            itineraries=itineraries,
            departure_times_list=departure_times,
        )
        print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
        print(json.dumps(engine.summary(), indent=2))
        if args.map:
            for aid, res in results.items():
                j = res.journey
                if j.get("steps"):
                    print(f"\nAgent: {aid}")
                    _print_agent_reasoning(j)
    else:
        for a in agent_list:
            loc_types = [x[0] for x in a["itinerary_locations"]]
            print(
                f"\n{'#'*60}\n# {a['id']}: {' \u2192 '.join(loc_types)}\n{'#'*60}"
            )
            for leg in range(len(a["itinerary_locations"]) - 1):
                loc_from = a["itinerary_locations"][leg]
                loc_to = a["itinerary_locations"][leg + 1]
                origin = (loc_from[1].lat, loc_from[1].lng)
                dest = (loc_to[1].lat, loc_to[1].lng)

                result = engine.run_agent(
                    a["id"],
                    a["profile"],
                    a["home"],
                    a["work"],
                    personality_self_intro=a["personality"]["self_intro"],
                    personality_travel_plans=a["personality"]["travel_plans"],
                    personality_context=a["personality"]["context"],
                )
                res = engine.results.get(a["id"])
                if res:
                    _print_agent_reasoning(res.journey)

        print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
        print(json.dumps(engine.summary(), indent=2))

    all_results = results if args.tick_mode else engine.results

    if recorder:
        rec_path = recorder.save("simulation")
        recorder.save_ticks_csv("ticks")

        if args.map:
            try:
                from visualization.traffic_map import generate_traffic_map

                map_path = generate_traffic_map(
                    recording_path=rec_path,
                    output_path=f"{args.output_dir}/traffic_map.html",
                    jam_density_per_km=(
                        cfg.traffic.jam_density_per_km
                        if hasattr(cfg, "traffic")
                        else 50.0
                    ),
                )
                if map_path:
                    print(f"\n  Traffic map: file://{map_path}")
            except Exception as e:
                logging.warning(f"Failed to generate traffic map: {e}")

            from visualization.path_map import generate_path_map
            for aid, res in all_results.items():
                j = res.journey
                if not j.get("steps"):
                    continue
                try:
                    pm = generate_path_map(
                        j,
                        output_path=f"{args.output_dir}/path_{aid}.html",
                    )
                    if pm:
                        print(f"  Path map {aid}: file://{pm}")
                except Exception as e:
                    logging.warning(f"Failed to generate path map for {aid}: {e}")


if __name__ == "__main__":
    main()
