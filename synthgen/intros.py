import json
import yaml
from pathlib import Path
from typing import Tuple

from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Ollama
from timeout_retry import invoke_with_retry, TimeoutError


def generate_intros(config_yaml: str | Path, prompts_yaml: str | Path, descriptions_jsonl: Path, narratives_jsonl: Path, output_jsonl: Path, verbose: bool = False, debug: bool = False):
    """Generate first-person introductions from descriptions and narratives."""
    print("Generating intros...")

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    demographic_inclusion = config.get("agents", {}).get("demographic_inclusion_in_intros_diaries", "narrative")

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

    with open(prompts_yaml, 'r') as f:
        prompts = yaml.safe_load(f)

    intro_prompt_text = prompts.get("intro", "")

    descriptions_dict = {}
    with open(descriptions_jsonl, 'r') as f:
        for obj in json.load(f):
            descriptions_dict[obj["agent_id"]] = obj["description"]

    with open(narratives_jsonl, 'r') as f:
        narratives = json.load(f)

    llm = Ollama(
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        num_predict=num_predict
    )

    include_demographics = demographic_inclusion in ("narrative", "both")

    if include_demographics:
        prompt_template = PromptTemplate(
            input_variables=["description", "narrative"],
            template=intro_prompt_text
        )
    else:
        prompt_template = PromptTemplate(
            input_variables=["narrative"],
            template=intro_prompt_text.replace("{description}", "").replace("Demographic profile: ", "").strip()
        )

    errors = []
    intros_written = 0
    intros_list = []
    n_narratives = len(narratives)

    for narrative_obj in narratives:
        agent_id = narrative_obj["agent_id"]
        narrative = narrative_obj["narrative"]
        description = descriptions_dict.get(agent_id, "") if include_demographics else ""

        try:
            if include_demographics and not description:
                raise ValueError("No description found")

            if include_demographics:
                prompt = prompt_template.format(description=description, narrative=narrative)
            else:
                prompt = prompt_template.format(narrative=narrative)
            intro = invoke_with_retry(llm, prompt, timeout_seconds, max_retries)

            if not intro or len(intro) < 10:
                raise ValueError("Generated intro too short")

            output = {"agent_id": agent_id, "intro": intro}
            if debug:
                output["debug_prompt"] = prompt
            intros_list.append(output)
            intros_written += 1

            if verbose and intros_written <= 5:
                print(f"\n--- Agent {agent_id} ---")
                print(intro)

        except (TimeoutError, Exception) as e:
            errors.append({"agent_id": agent_id, "error": str(e)})

    with open(output_jsonl, 'w') as out:
        json.dump(intros_list, out, indent=2)

    print(f"Generated {intros_written}/{n_narratives} intros")

    if errors:
        print(f"Errors: {len(errors)} agents skipped")
        errors_path = output_jsonl.parent / "intro_errors.json"
        with open(errors_path, 'w') as f:
            json.dump(errors, f, indent=2)
        print(f"Saved errors to {errors_path}")
