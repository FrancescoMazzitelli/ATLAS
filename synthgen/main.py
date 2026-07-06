import pandas as pd
import yaml
import json
import sys
import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Tuple

from population import generate_questions, synthesize_population, get_demographic_data, get_attr_description
from incidents import crashes_to_jsonl
from narratives import generate_narratives
from intros import generate_intros
from diaries import generate_diaries


def generate_agent_descriptions(
    config_yaml: str | Path,
    data_path: str | Path,
    n_sample: int = 10,
    head: int | None = None,
    min_age: int = 18,
    max_age: int = 65,
    verbose: bool = False,
) -> Tuple[list[str], pd.DataFrame]:
    """
    Generate textual descriptions for a synthetic population sample.

    This function synthesizes a population, filters it by age constraints, and
    generates human-readable descriptions for each person based on their demographic attributes.

    Parameters
    ----------
    config_yaml : str | Path
        Path to the YAML configuration file.
    data_path : str | Path
        Path to demographic data directory used by get_demographic_data.
    n_sample : int, default=10
        Number of synthetic population samples to generate.
    head : int | None, default=None
        If provided, only generate descriptions for the first `head` rows.
    min_age : int, default=18
        Minimum age for the population sample.
    max_age : int, default=65
        Maximum age for the population sample.

    Returns
    -------
    tuple[list[str], pd.DataFrame]
        A tuple containing:
        - List of textual descriptions for each agent
        - The full population sample DataFrame
    """
    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    synth = SimpleNamespace(**config.get('synth', {}))

    generate_questions(config_yaml, source=synth.source)

    population = synthesize_population(
        config_yaml=config_yaml,
        n_sample=n_sample,
        min_age=min_age,
        max_age=max_age,
    )

    population = population[population.AGEP.astype(int) > min_age]

    demographic_variables = [
        "AGEP", "ANC1P", "CIT", "CITWP", "COW", "DDRS", "DEAR", "DEYE", "DOUT",
        "DPHY", "DREM", "ESR", "FOD1P", "HICOV", "HINS1", "HINS2", "HINS3",
        "HINS4", "HINS5", "HINS6", "HINS7", "HISP", "LANP", "MAR", "MARHD",
        "MARHYP", "MIL", "NAICSP", "PAOC", "PERNP", "PINCP", "POBP", "POVPIP",
        "POWPUMA", "PRIVCOV", "PUBCOV", "PUMA", "RAC1P", "RAC2P", "RAC3P",
        "RACNUM", "RELSHIPP", "RETP", "SCH", "SCHG", "SCHL", "SCL", "SEX",
        "SSIP", "SSP", "VPS", "WAGP", "WKHP", "WKWN", "YEAR", "YOEP",
    ]

    available_vars = [col for col in population.columns if col in demographic_variables]
    sample = population[available_vars]

    if head is not None:
        sample = sample.iloc[:head]

    _, mapper = get_demographic_data(data_path)

    descriptions = [
        get_attr_description(list(sample.columns), row, mapper)
        for _, row in sample.iterrows()
    ]

    if verbose:
        print("\nFirst 5 agents:")
        for i, desc in enumerate(descriptions[:5], 1):
            print(f"\n--- Agent {i} ---")
            print(desc)

        print("\n\nFirst 5 population records:")
        print(sample.head(5).reset_index(drop=True).to_string())

    return descriptions, population


def combine_agents_with_checkpoints(descriptions_json: Path, narratives_json: Path, intros_json: Path, diaries_json: Path, output_json: Path, config_yaml: str | Path | None = None, verbose: bool = False):
    """Combine agents with periodic checkpointing every N agents."""
    checkpoint_frequency = 20
    if config_yaml:
        with open(config_yaml, 'r') as f:
            config = yaml.safe_load(f)
        checkpoint_frequency = config.get("agents", {}).get("checkpoint_frequency", 20)

    if checkpoint_frequency <= 0:
        # No checkpointing, just combine normally
        combine_agents(descriptions_json, narratives_json, intros_json, diaries_json, output_json, config_yaml, verbose)
        return

    # Load all data
    descriptions_list = []
    if descriptions_json.exists():
        with open(descriptions_json, 'r') as f:
            descriptions_list = json.load(f)

    narratives_list = []
    if narratives_json.exists():
        with open(narratives_json, 'r') as f:
            narratives_list = json.load(f)

    intros_list = []
    if intros_json.exists():
        with open(intros_json, 'r') as f:
            intros_list = json.load(f)

    diaries_list = []
    if diaries_json.exists():
        with open(diaries_json, 'r') as f:
            diaries_list = json.load(f)

    total_agents = len(descriptions_list)
    checkpoint_files = []

    # Process in chunks and create checkpoint files
    for chunk_start in range(0, total_agents, checkpoint_frequency):
        chunk_end = min(chunk_start + checkpoint_frequency, total_agents)
        checkpoint_num = chunk_start // checkpoint_frequency

        if verbose:
            print(f"Creating checkpoint {checkpoint_num}: agents {chunk_start}-{chunk_end - 1}")

        # Combine this chunk
        agents = {}

        for desc in descriptions_list[chunk_start:chunk_end]:
            agent_id = desc["agent_id"]
            agents[agent_id] = {"agent_idx": agent_id, "description": desc["description"]}

        for narr in narratives_list:
            agent_id = narr["agent_id"]
            if agent_id in agents:
                agents[agent_id]["travel_plans_summary"] = narr["narrative"]
                agents[agent_id]["mood"] = narr["mood"]
                if "debug_prompt" in narr:
                    agents[agent_id]["debug_narrative_prompt"] = narr["debug_prompt"]

        for intro in intros_list:
            agent_id = intro["agent_id"]
            if agent_id in agents:
                agents[agent_id]["self_introduction"] = intro["intro"]
                if "debug_prompt" in intro:
                    agents[agent_id]["debug_intro_prompt"] = intro["debug_prompt"]

        for diary in diaries_list:
            agent_id = diary["agent_id"]
            if agent_id in agents:
                agents[agent_id]["itinerary"] = {
                    "locations": diary["locations"],
                    "location_context": diary["contexts"],
                    "departure_times": diary["departures"],
                }
                if "debug_prompt" in diary:
                    agents[agent_id]["debug_diary_prompt"] = diary["debug_prompt"]

        # Add demographics footer if needed
        demographic_inclusion = "narrative"
        if config_yaml:
            with open(config_yaml, 'r') as f:
                config = yaml.safe_load(f)
            demographic_inclusion = config.get("agents", {}).get("demographic_inclusion", "narrative")

        if demographic_inclusion in ("footer", "both"):
            for agent_id in agents:
                matching_desc = next((d for d in descriptions_list if d["agent_id"] == agent_id), None)
                if matching_desc:
                    agents[agent_id]["_sociodemographic_data"] = {
                        "note": "These sociodemographic values were used to generate the narratives",
                        "profile": matching_desc["description"]
                    }

        # Write checkpoint file
        checkpoint_path = output_json.parent / f"{output_json.stem}.cache_{checkpoint_num}"
        agents_list = [agents[aid] for aid in sorted(agents.keys())]
        with open(checkpoint_path, 'w') as f:
            for agent in agents_list:
                f.write(json.dumps(agent) + "\n")
        checkpoint_files.append(checkpoint_path)

    if verbose:
        print(f"Created {len(checkpoint_files)} checkpoint files")

    # Combine all checkpoints into final file
    if verbose:
        print("Combining checkpoints into final file...")

    final_agents = []
    for checkpoint_path in checkpoint_files:
        with open(checkpoint_path, 'r') as f:
            final_agents.extend(json.loads(line) for line in f if line.strip())

    with open(output_json, 'w') as f:
        for agent in final_agents:
            f.write(json.dumps(agent) + "\n")

    if verbose:
        print(f"Wrote final agents file: {output_json}")

    # Delete checkpoint files
    if verbose:
        print("Cleaning up checkpoint files...")
    for checkpoint_path in checkpoint_files:
        checkpoint_path.unlink()
        if verbose:
            print(f"Deleted {checkpoint_path}")


def combine_agents(descriptions_json: Path, narratives_json: Path, intros_json: Path, diaries_json: Path, output_json: Path, config_yaml: str | Path | None = None, verbose: bool = False):
    """Combine descriptions, narratives, intros, and diaries into a single agents.jsonl file."""
    agents = {}
    demographic_inclusion = "narrative"

    if config_yaml:
        with open(config_yaml, 'r') as f:
            config = yaml.safe_load(f)
        demographic_inclusion = config.get("agents", {}).get("demographic_inclusion", "narrative")

    descriptions_by_id = {}
    if descriptions_json.exists():
        with open(descriptions_json, 'r') as f:
            for obj in json.load(f):
                agent_id = obj["agent_id"]
                descriptions_by_id[agent_id] = obj["description"]
                agents[agent_id] = {"agent_idx": agent_id, "description": obj["description"]}

    if narratives_json.exists():
        with open(narratives_json, 'r') as f:
            for obj in json.load(f):
                agent_id = obj["agent_id"]
                if agent_id in agents:
                    agents[agent_id]["travel_plans_summary"] = obj["narrative"]
                    agents[agent_id]["mood"] = obj["mood"]
                    if "debug_prompt" in obj:
                        agents[agent_id]["debug_narrative_prompt"] = obj["debug_prompt"]

    if intros_json.exists():
        with open(intros_json, 'r') as f:
            for obj in json.load(f):
                agent_id = obj["agent_id"]
                if agent_id in agents:
                    agents[agent_id]["self_introduction"] = obj["intro"]
                    if "debug_prompt" in obj:
                        agents[agent_id]["debug_intro_prompt"] = obj["debug_prompt"]

    if diaries_json.exists():
        with open(diaries_json, 'r') as f:
            for obj in json.load(f):
                agent_id = obj["agent_id"]
                if agent_id in agents:
                    agents[agent_id]["itinerary"] = {
                        "locations": obj["locations"],
                        "location_context": obj["contexts"],
                        "departure_times": obj["departures"],
                    }
                    if "debug_prompt" in obj:
                        agents[agent_id]["debug_diary_prompt"] = obj["debug_prompt"]

    if demographic_inclusion in ("footer", "both"):
        for agent_id in agents:
            if agent_id in descriptions_by_id:
                agents[agent_id]["_sociodemographic_data"] = {
                    "note": "These sociodemographic values were used to generate the narratives",
                    "profile": descriptions_by_id[agent_id]
                }

    agents_list = [agents[agent_id] for agent_id in sorted(agents.keys())]
    with open(output_json, 'w') as f:
        for agent in agents_list:
            f.write(json.dumps(agent) + "\n")

    if verbose:
        print(f"Combined {len(agents)} agents into {output_json}")


def create_incidents(config_yaml: str | Path, run_desc_dir: Path, verbose: bool = False, population_df: pd.DataFrame | None = None):
    """Create incidents for population agents."""
    print("Creating incidents...")

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)
    config_dir = Path(config_yaml).parent
    data_path = config_dir / config.get("data", {}).get("base_path", "data")

    crashes_files = list(data_path.glob("Traffic_*Crashes_*.csv"))
    if not crashes_files:
        raise FileNotFoundError(f"No Traffic_*Crashes_*.csv found in {data_path}")
    crashes_csv = crashes_files[0]
    df_crashes = pd.read_csv(crashes_csv, low_memory=False)

    output_jsonl = run_desc_dir / "incidents.jsonl"
    incidents_df = crashes_to_jsonl(df_crashes, str(output_jsonl))

    if verbose:
        print("\nFirst 5 incidents:")
        print(incidents_df.head(5).to_string())

    print(f"Saved incidents to {output_jsonl}")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic population agents and incidents")
    parser.add_argument("config_yaml", help="Path to YAML configuration file")
    parser.add_argument("run_path", help="Path to create run/run_desc folders")
    parser.add_argument("--generate-population", action="store_true", help="Generate population descriptions")
    parser.add_argument("--create-incidents", action="store_true", help="Create incidents for population")
    parser.add_argument("--generate-narratives", action="store_true", help="Generate narratives from population descriptions")
    parser.add_argument("--generate-diaries", action="store_true", help="Generate travel diaries from narratives")
    parser.add_argument("--verbose", action="store_true", help="Print verbose output")
    parser.add_argument("--debug", action="store_true", help="Include formatted prompts in output")

    args = parser.parse_args()

    run_desc_dir = Path(args.run_path)
    run_desc_dir.mkdir(parents=True, exist_ok=True)

    with open(args.config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    config_dir = Path(args.config_yaml).parent
    data_path = config_dir / config.get("data", {}).get("base_path", "data")
    n_sample = config.get("agents", {}).get("n_sample", 10)

    descriptions_json = run_desc_dir / "population_descriptions.json"
    population_csv = run_desc_dir / "population.csv"
    narratives_json = run_desc_dir / "narratives.json"
    intros_json = run_desc_dir / "intros.json"
    diaries_json = run_desc_dir / "diaries.json"
    combined_json = run_desc_dir / "agents.jsonl"
    prompts_yaml = Path(__file__).parent / "prompts.yaml"

    if args.verbose:
        print(f"Run folder: {run_desc_dir}\n")
        if descriptions_json.exists():
            print(f"✓ population_descriptions.json")
        if population_csv.exists():
            print(f"✓ population.csv")
        if narratives_json.exists():
            print(f"✓ narratives.json")
        if intros_json.exists():
            print(f"✓ intros.json")
        if diaries_json.exists():
            print(f"✓ diaries.json")
        if any([descriptions_json.exists(), population_csv.exists(), narratives_json.exists(), intros_json.exists(), diaries_json.exists()]):
            print()

    if not args.generate_population and not args.create_incidents and not args.generate_narratives and not args.generate_diaries:
        args.generate_population = not descriptions_json.exists() or not population_csv.exists()
        args.generate_narratives = not narratives_json.exists()
        args.generate_diaries = not diaries_json.exists() or not intros_json.exists()

    if args.generate_population or not descriptions_json.exists() or not population_csv.exists():
        if args.verbose and (descriptions_json.exists() or population_csv.exists()):
            print("Skipping population generation (files exist)")
            print()
        else:
            print(f"Generating agent descriptions...")
        print(f"  Config: {args.config_yaml}")
        print()

        descriptions, population = generate_agent_descriptions(
            config_yaml=args.config_yaml,
            data_path=data_path,
            n_sample=max(n_sample, 100),
            head=n_sample,
            verbose=args.verbose,
        )

        descriptions_list = [{"agent_id": i, "description": desc} for i, desc in enumerate(descriptions)]
        with open(descriptions_json, "w") as f:
            json.dump(descriptions_list, f, indent=2)

        population_with_id = population.copy()
        population_with_id.insert(0, "agent_id", range(len(population_with_id)))
        population_with_id.to_csv(population_csv, index=False)

        print(f"Generated {len(descriptions)} descriptions")
        print(f"Population sample shape: {population.shape}")
        print(f"Saved to {descriptions_json}")
        print(f"Saved to {population_csv}")

        if args.create_incidents:
            create_incidents(args.config_yaml, run_desc_dir, verbose=args.verbose, population=population)

    if args.generate_narratives or not narratives_json.exists():
        if not descriptions_json.exists():
            print("Error: population_descriptions.json not found. Run --generate-population first.")
            return
        if args.verbose and narratives_json.exists():
            print("Skipping narrative generation (files exist)")
            print()
        else:
            generate_narratives(args.config_yaml, descriptions_json, narratives_json, prompts_yaml=prompts_yaml, verbose=args.verbose, debug=args.debug)

    if args.generate_diaries or not intros_json.exists():
        if not descriptions_json.exists():
            print("Error: population_descriptions.json not found. Run --generate-population first.")
            return
        if not narratives_json.exists():
            print("Error: narratives.json not found. Run --generate-narratives first.")
            return
        if args.verbose and intros_json.exists():
            print("Skipping intro generation (files exist)")
            print()
        else:
            generate_intros(args.config_yaml, prompts_yaml, descriptions_json, narratives_json, intros_json, verbose=args.verbose, debug=args.debug)

    if args.generate_diaries or not diaries_json.exists():
        if not narratives_json.exists():
            print("Error: narratives.json not found. Run --generate-narratives first.")
            return
        if not intros_json.exists():
            print("Error: intros.json not found. Run intro generation first.")
            return
        if not population_csv.exists():
            print("Error: population.csv not found. Run --generate-population first.")
            return
        if args.verbose and diaries_json.exists():
            print("Skipping diary generation (files exist)")
            print()
        else:
            generate_diaries(args.config_yaml, narratives_json, intros_json, population_csv, diaries_json, prompts_yaml=prompts_yaml, descriptions_jsonl=descriptions_json, verbose=args.verbose, debug=args.debug)

    if args.verbose:
        print("Combining all agent data into single file...")
    combine_agents_with_checkpoints(descriptions_json, narratives_json, intros_json, diaries_json, combined_json, config_yaml=args.config_yaml, verbose=args.verbose)


if __name__ == "__main__":
    main()
