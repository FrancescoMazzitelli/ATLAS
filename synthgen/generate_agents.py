import pandas as pd
import yaml
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Tuple

from preprocess import generate_questions
from synthesize import synthesize_population
from persona import get_demographic_data, get_attr_description


def generate_agent_descriptions(
    config_yaml: str | Path,
    data_path: str | Path,
    n_sample: int = 10,
    head: int | None = None,
    min_age: int = 18,
    max_age: int = 65,
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

    return descriptions, population


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_agents.py <config_yaml> [n_sample] [head]")
        print("  config_yaml: Path to YAML configuration file")
        print("  n_sample: Number of samples to generate (default: 10)")
        print("  head: Number of descriptions to generate (default: all)")
        sys.exit(1)

    config_yaml = sys.argv[1]
    n_sample = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    head = int(sys.argv[3]) if len(sys.argv) > 3 else None

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    config_dir = Path(config_yaml).parent
    data_path = config_dir / config.get("data", {}).get("base_path", "data")

    print(f"Generating agent descriptions...")
    print(f"  Config: {config_yaml}")
    print(f"  Samples: {n_sample}")
    print(f"  Head: {head}")
    print()

    descriptions, population = generate_agent_descriptions(
        config_yaml=config_yaml,
        data_path=data_path,
        n_sample=n_sample,
        head=head,
    )

    print(f"Generated {len(descriptions)} descriptions\n")
    for i, desc in enumerate(descriptions, 1):
        print(f"--- Agent {i} ---")
        print(desc)
        print()

    print(f"Population sample shape: {population.shape}")


if __name__ == "__main__":
    main()
