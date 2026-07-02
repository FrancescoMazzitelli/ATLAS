import json
import random
from pathlib import Path
from typing import Dict, Tuple
import yaml

from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Ollama
from timeout_retry import invoke_with_retry, TimeoutError


def load_and_sample_moods(config_yaml: str | Path, n_agents: int) -> list[str]:
    """Load mood distribution from config and sample for each agent."""
    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    mood_dist = config.get("mood", {})
    moods = list(mood_dist.keys())
    weights = [mood_dist[m] for m in moods]

    sampled_moods = random.choices(moods, weights=weights, k=n_agents)
    return sampled_moods


def generate_narratives(config_yaml: str | Path, descriptions_jsonl: Path, output_jsonl: Path, prompts_yaml: str | Path | None = None, verbose: bool = False, debug: bool = False):
    """Generate rich second-person narratives from population descriptions."""
    print("Generating narratives...")

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

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

    with open(descriptions_jsonl, 'r') as f:
        descriptions = json.load(f)

    n_agents = len(descriptions)
    moods = load_and_sample_moods(config_yaml, n_agents)

    demographic_inclusion = config.get("agents", {}).get("demographic_inclusion", "narrative")
    demographic_percent = config.get("agents", {}).get("demographic_inclusion_percent", 0.6)

    llm = Ollama(
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        num_predict=num_predict
    )

    narrative_prompt_text = prompts.get("narrative", "")

    demographic_instruction = ""
    if demographic_inclusion in ("narrative", "both"):
        demographic_instruction = f"\n\nIMPORTANT: Weave at least {int(demographic_percent * 100)}% of the provided demographic attributes directly into your narrative. Be specific: mention age range, occupation type, family status, education level, or other key demographics as they naturally fit the character description."

    prompt_template = PromptTemplate(
        input_variables=["description", "mood", "demographic_instruction"],
        template=narrative_prompt_text
    )

    errors = []
    narratives_written = 0
    narratives_list = []

    for i, (desc_obj, mood) in enumerate(zip(descriptions, moods)):
        agent_id = desc_obj["agent_id"]
        description = desc_obj["description"]

        try:
            prompt = prompt_template.format(description=description, mood=mood, demographic_instruction=demographic_instruction)
            narrative = invoke_with_retry(llm, prompt, timeout_seconds, max_retries)

            if not narrative or len(narrative) < 20:
                raise ValueError("Generated narrative too short")

            output = {"agent_id": agent_id, "narrative": narrative, "mood": mood}
            if debug:
                output["debug_prompt"] = prompt
            narratives_list.append(output)
            narratives_written += 1

            if verbose and i < 5:
                print(f"\n--- Agent {agent_id} ({mood}) ---")
                print(narrative)

        except (TimeoutError, Exception) as e:
            errors.append({"agent_id": agent_id, "error": str(e)})

    with open(output_jsonl, 'w') as out:
        json.dump(narratives_list, out, indent=2)

    print(f"Generated {narratives_written}/{n_agents} narratives")

    if errors:
        print(f"Errors: {len(errors)} agents failed")
        errors_path = output_jsonl.parent / "narrative_errors.json"
        with open(errors_path, 'w') as f:
            json.dump(errors, f, indent=2)
        print(f"Saved errors to {errors_path}")
