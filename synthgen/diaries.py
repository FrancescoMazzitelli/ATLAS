import json
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from typing import Tuple
import pandas as pd

from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Ollama
from timeout_retry import invoke_with_retry, TimeoutError


def validate_diary(diary_dict: dict) -> Tuple[bool, str]:
    """Validate diary structure and logic."""
    try:
        locations = diary_dict.get("locations", [])
        contexts = diary_dict.get("contexts", [])
        departures = diary_dict.get("departures", [])

        if not locations or not contexts:
            return False, "Missing locations or contexts"

        if len(locations) != len(contexts):
            return False, f"Locations ({len(locations)}) and contexts ({len(contexts)}) mismatch"

        if len(departures) != len(locations) - 1:
            return False, f"Departures ({len(departures)}) should be one less than locations ({len(locations)})"

        valid_locations = {"HOME", "WORK", "SCHOOL", "DISCRETIONARY"}
        for loc in locations:
            if loc not in valid_locations:
                return False, f"Invalid location: {loc}"

        for i in range(1, len(locations)):
            if locations[i] == locations[i-1]:
                return False, f"Consecutive duplicate locations: {locations[i]}"

        for i, dep in enumerate(departures):
            if not dep or dep.count(":") != 1:
                return False, f"Invalid departure time format: {dep}"

            try:
                h, m = dep.split(":")
                int(h)
                int(m)
            except:
                return False, f"Invalid departure time: {dep}"

        for i in range(len(departures) - 1):
            current = datetime.strptime(departures[i], "%H:%M")
            next_dep = datetime.strptime(departures[i+1], "%H:%M")

            diff = (next_dep - current).total_seconds() / 3600
            if diff < 1 and diff > -23:
                return False, f"Time gap {diff:.1f}h between departure {i} and {i+1} (min 1h)"

        return True, ""

    except Exception as e:
        return False, str(e)


def generate_diaries(config_yaml: str | Path, narratives_jsonl: Path, intros_jsonl: Path, population_csv: Path, output_jsonl: Path, prompts_yaml: str | Path | None = None, descriptions_jsonl: Path | None = None, verbose: bool = False, debug: bool = False):
    """Generate travel diaries from narratives and intros."""
    print("Generating diaries...")

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    demographic_inclusion = config.get("agents", {}).get("demographic_inclusion_in_intros_diaries", "narrative")

    if prompts_yaml is None:
        prompts_yaml = Path(config_yaml).parent / "prompts.yaml"

    with open(prompts_yaml, 'r') as f:
        prompts = yaml.safe_load(f)

    ollama_config = config.get("ollama")
    if not ollama_config:
        raise ValueError("ollama config not found in config.yaml")

    model_name = ollama_config.get("model")
    temperature = ollama_config.get("temperature")
    top_p = ollama_config.get("top_p")
    num_predict = ollama_config.get("num_predict")
    timeout_seconds = ollama_config.get("timeout_seconds", 120)
    max_retries = ollama_config.get("max_retries", 2)

    if not model_name:
        raise ValueError("ollama.model not specified in config.yaml")
    if temperature is None:
        raise ValueError("ollama.temperature not specified in config.yaml")
    if top_p is None:
        raise ValueError("ollama.top_p not specified in config.yaml")
    if num_predict is None:
        raise ValueError("ollama.num_predict not specified in config.yaml")

    with open(narratives_jsonl, 'r') as f:
        narratives = json.load(f)

    intros_dict = {}
    with open(intros_jsonl, 'r') as f:
        for obj in json.load(f):
            intros_dict[obj["agent_id"]] = obj["intro"]

    include_demographics = demographic_inclusion in ("narrative", "both")
    descriptions_dict = {}
    if include_demographics and descriptions_jsonl:
        with open(descriptions_jsonl, 'r') as f:
            for obj in json.load(f):
                descriptions_dict[obj["agent_id"]] = obj["description"]

    population = pd.read_csv(population_csv)

    llm = Ollama(
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        num_predict=num_predict
    )

    diary_prompt_text = prompts.get("diary", "")

    if include_demographics:
        prompt_template = PromptTemplate(
            input_variables=["narrative", "intro", "description"],
            template=diary_prompt_text
        )
    else:
        prompt_template = PromptTemplate(
            input_variables=["narrative", "intro"],
            template=diary_prompt_text.replace("{description}", "").strip()
        )

    errors = []
    diaries_written = 0
    diaries_list = []
    n_narratives = len(narratives)

    for narrative_obj in narratives:
        agent_id = narrative_obj["agent_id"]
        narrative = narrative_obj["narrative"]

        success = False
        last_error = None

        intro = intros_dict.get(agent_id, "")
        if not intro:
            errors.append({"agent_id": agent_id, "error": "No intro found"})
            continue

        try:
            description = descriptions_dict.get(agent_id, "") if include_demographics else ""
            if include_demographics:
                prompt = prompt_template.format(narrative=narrative, intro=intro, description=description)
            else:
                prompt = prompt_template.format(narrative=narrative, intro=intro)
            response = invoke_with_retry(llm, prompt, timeout_seconds, max_retries)

            try:
                diary_dict = json.loads(response)
            except json.JSONDecodeError as e:
                raise ValueError(f"LLM did not return valid JSON: {response[:100]}")

            valid, error_msg = validate_diary(diary_dict)
            if not valid:
                raise ValueError(f"Validation failed: {error_msg}")

            diary_dict["agent_id"] = agent_id
            if debug:
                diary_dict["debug_prompt"] = prompt
            diaries_list.append(diary_dict)
            diaries_written += 1

            if verbose and diaries_written <= 5:
                print(f"\n--- Agent {agent_id} ---")
                print(f"Locations: {diary_dict['locations']}")
                print(f"Departures: {diary_dict['departures']}")

        except (TimeoutError, Exception) as e:
            errors.append({"agent_id": agent_id, "error": str(e)})

    with open(output_jsonl, 'w') as out:
        json.dump(diaries_list, out, indent=2)

    print(f"Generated {diaries_written}/{len(narratives)} diaries")

    if errors:
        print(f"Errors: {len(errors)} agents skipped")
        errors_path = output_jsonl.parent / "diary_errors.json"
        with open(errors_path, 'w') as f:
            json.dump(errors, f, indent=2)
        print(f"Saved errors to {errors_path}")
