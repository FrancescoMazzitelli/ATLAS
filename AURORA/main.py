import logging
import json
import argparse
import os
from typing import Optional, Tuple

from agentic.core.models import SociodemographicProfile, Location
from simulation.engine import SimulationEngine
from simulation.recorder import SimulationRecorder
from simulation.clock import SimulationClock
from simulation.config import load_config
from geocoding.nominatim import NominatimClient


CHICAGO_DEFAULT = (41.8781, -87.6298)
CHICAGO_HOME_DEFAULT = (41.88, -87.63)


def _load_agents_from_jsonl(
    jsonl_path: str,
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

    def _entry_coords(entry: dict, loc_type: str, idx: int) -> Tuple[float, float, str]:
        itinerary = entry.get("itinerary", {})
        coords = itinerary.get("coordinates")
        if coords and idx < len(coords):
            c = coords[idx]
            lat = float(c.get("lat", c.get("y", CHICAGO_DEFAULT[0])))
            lon = float(c.get("lon", c.get("lng", c.get("x", CHICAGO_DEFAULT[1]))))
            name = None
            if nominatim:
                name = nominatim.reverse_name(lat, lon)
            return lat, lon, name or f"{loc_type.lower()}_{idx}"

        locs = itinerary.get("locations", [])
        if idx < len(locs):
            _type = locs[idx]
            if _type == "HOME":
                return (CHICAGO_HOME_DEFAULT[0], CHICAGO_HOME_DEFAULT[1], "Home")
            elif _type in ("WORK", "SCHOOL"):
                return (CHICAGO_DEFAULT[0], CHICAGO_DEFAULT[1], "Workplace")
        return (CHICAGO_DEFAULT[0], CHICAGO_DEFAULT[1], "Misc")

    agent_list = []
    for entry in entries:
        aid = f"agent_{entry['agent_idx']:04d}"
        bio = entry.get("self_introduction", "")
        locs = entry.get("itinerary", {}).get("locations", [])
        contexts = entry.get("itinerary", {}).get("location_context", [])

        if not locs:
            continue

        resolved = []
        for i, lt in enumerate(locs):
            lat, lon, name = _entry_coords(entry, lt, i)
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

        agent_list.append({
            "id": aid,
            "profile": profile,
            "home": home,
            "work": work,
            "bio": bio,
            "itinerary": list(zip(locs, contexts, resolved)),
            "personality": {
                "self_intro": entry.get("self_introduction", ""),
                "travel_plans": entry.get("travel_plans_summary", ""),
                "context": list(zip(locs, contexts)),
            },
        })

    logging.info(f"Loaded {len(agent_list)} agents from {jsonl_path}")
    return agent_list


def _run_itinerary(engine, agent, output_dir: str, recorder=None):
    """Run each leg of the agent's itinerary through the simulation."""
    aid = agent["id"]
    itinerary = agent["itinerary"]
    pers = agent["personality"]

    results = []
    all_steps = []
    tick_offset = 0

    for leg in range(len(itinerary) - 1):
        loc_from = itinerary[leg]
        loc_to = itinerary[leg + 1]
        origin = (loc_from[2][1], loc_from[2][2])
        dest = (loc_to[2][1], loc_to[2][2])

        result = engine.run_agent(
            aid,
            agent["profile"],
            agent["home"],
            agent["work"],
            personality_self_intro=pers["self_intro"],
            personality_travel_plans=pers["travel_plans"],
            personality_context=pers["context"],
        )
        results.append(result)
        all_steps.append(result.journey.get("steps", []))

    # Populate recorder for traffic map even in sequential mode
    if recorder:
        from simulation.recorder import TickRecord
        recorder.register_agent(aid, agent["profile"], agent["bio"], agent["home"], agent["work"])
        for leg_idx, steps in enumerate(all_steps):
            for step_i, step in enumerate(steps):
                frm = step.get("from")
                if frm and len(frm) >= 2 and frm[0] is not None:
                    rec = TickRecord(tick=tick_offset + step_i, timestamp="")
                    rec.agents[aid] = {
                        "position": list(frm),
                        "destination": list(itinerary[leg_idx + 1][2][1:3]) if leg_idx + 1 < len(itinerary) else None,
                        "is_traveling": step_i < len(steps) - 1,
                        "arrived": step_i == len(steps) - 1 and leg_idx == len(all_steps) - 1,
                        "step_count": step["index"] + 1,
                        "route_edge_count": 0,
                    }
                    recorder.ticks.append(rec)
            tick_offset += len(steps)
        final_journey = results[-1].journey if results else {}
        recorder.finalize_agent(aid, final_journey.get("status", "arrived"),
                                sum(len(s) for s in all_steps), journey=final_journey)

    return results


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
        print(f"      ({frm[0]:.4f},{frm[1]:.4f}) → ({to[0]:.4f},{to[1]:.4f})")


def main():
    parser = argparse.ArgumentParser(
        description="AURORA — Autonomous Urban Reasoning and Optimized Routing Adaptation"
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
        max_agents=max_n or cfg.data.num_agents,
        nominatim=nominatim,
    )

    if args.list_agents:
        print(f"Loaded {len(agent_list)} agents:\n")
        for a in agent_list:
            locs = [x[0] for x in a["itinerary"]]
            print(f"  {a['id']}: {a['bio'][:60]}...")
            print(f"       Itinerary: {' → '.join(locs)}")
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
        log_level=cfg.simulation.log_level,
        recursion_limit=cfg.simulation.recursion_limit,
        disruption_files=list(cfg.simulation.disruption_files),
        recorder=recorder,
    )

    if args.tick_mode:
        clock = SimulationClock(
            start_datetime=__import__("datetime").datetime.fromisoformat(
                cfg.simulation.clock.start_datetime
            ),
            tick_duration_minutes=cfg.simulation.clock.tick_duration_minutes,
            max_ticks=cfg.simulation.clock.max_ticks,
        )
        agent_tuples = [(a["id"], a["profile"], a["home"], a["work"]) for a in agent_list]
        bios = [a["bio"] for a in agent_list]
        personalities = [a["personality"] for a in agent_list]
        results = engine.run(agent_tuples, clock, bios=bios, personalities=personalities)
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
            print(
                f"\n{'#'*60}\n# {a['id']}: {' → '.join(x[0] for x in a['itinerary'])}\n{'#'*60}"
            )
            _run_itinerary(engine, a, args.output_dir, recorder=recorder)
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
