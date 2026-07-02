import json
import logging
import re
from typing import List, Tuple, Optional

import requests as _requests
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)


_DECISION_CACHE: dict = {}


def _call_llm_decision(
    llm: ChatOllama,
    system: str,
    user: str,
) -> Optional[dict]:
    model = getattr(llm, 'model', 'qwen3.5:9b')
    temperature = getattr(llm, 'temperature', 0.5)
    num_predict = getattr(llm, 'num_predict', 4096)
    base = getattr(llm, 'base_url', None) or 'http://localhost:11434'
    url = f"{base.rstrip('/')}/api/generate"

    payload = {
        "model": model,
        "system": system,
        "prompt": user,
        "format": "json",
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }

    try:
        resp = _requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Discretionary LLM call failed: {e}")
        return None

    data = resp.json()
    content = data.get("response", "") or data.get("thinking", "")
    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    cleaned = re.sub(r'```json\s*|\s*```', '', cleaned).strip()

    try:
        obj = json.loads(cleaned)
        return obj
    except json.JSONDecodeError:
        matches = re.findall(r'\{[^}]+\}', cleaned)
        if matches:
            try:
                return json.loads(matches[0])
            except json.JSONDecodeError:
                pass
    return None


def ask_discretionary(
    agent_id: str,
    personality_self_intro: str,
    personality_travel_plans: str,
    personality_context: List[Tuple[str, str]],
    current_activity_context: str,
    discretionary_location_name: str,
    social_invitation_template: str,
    llm: ChatOllama,
    random_state: Optional[any] = None,
) -> bool:
    cache_key = f"{agent_id}:disc:{current_activity_context[:50]}"
    if cache_key in _DECISION_CACHE:
        return _DECISION_CACHE[cache_key]

    context_lines = "\n".join(
        f"  At {loc}: {ctx}" for loc, ctx in personality_context
    ) if personality_context else ""

    social_invitation = social_invitation_template.format(
        context=current_activity_context or "run a personal errand"
    )

    system = f"""You are {agent_id}.

YOUR PERSONALITY:
{personality_self_intro}

TODAY'S TRAVEL PLANS:
{personality_travel_plans}

YOUR ITINERARY CONTEXT:
{context_lines}

You have just finished your main obligations for the day (work, school, etc.).
Now you face a social choice.

{social_invitation}

Respond with a JSON object containing:
  "decision": "go" or "skip"
  "reasoning": "Brief explanation based on your personality, how tired you are, your social nature, and your plans."

IMPORTANT:
- Base your decision on YOUR personality traits — NOT as a utility-maximizer.
- If you are social, enjoy spending time with colleagues, and are not too tired, you might go.
- If you are tired, have family obligations, or prefer quiet time, you might skip.
- Output ONLY valid JSON, no other text."""

    user = "What do you decide? Output JSON with 'decision' and 'reasoning'."

    result = _call_llm_decision(llm, system, user)

    if result is None:
        logger.info(f"[{agent_id}] LLM discretionary decision failed, using default: skip")
        _DECISION_CACHE[cache_key] = False
        return False

    decision = result.get("decision", "skip")
    reasoning = result.get("reasoning", "")
    go = decision == "go"

    logger.info(
        f"[{agent_id}] discretionary decision: {'GO' if go else 'SKIP'} — {reasoning[:200]}"
    )
    _DECISION_CACHE[cache_key] = go
    return go
