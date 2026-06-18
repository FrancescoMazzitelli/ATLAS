from typing import TypedDict, List, Optional, Tuple, Dict, Any, Literal
from datetime import datetime
import json
import math
import re
import logging

import requests as _requests

from langgraph.graph import StateGraph, END, START
from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage

from routing.engine import ValhallaEngine, Route
from disruption.scenarios import Disruption

logger = logging.getLogger(__name__)

# ─── LLM caller (handles both reasoning and non-reasoning models) ───

import os as _os

_llm_logger = logging.getLogger("aurora.llm")
_llm_logger.setLevel(logging.INFO)
_llm_logger.handlers = []
_llm_log_path = _os.path.join("output", "llm_responses.log")
_os.makedirs("output", exist_ok=True)
_llm_handler = logging.FileHandler(_llm_log_path, mode="w")
_llm_handler.setFormatter(logging.Formatter("%(asctime)s\n%(message)s\n" + "=" * 60))
_llm_logger.addHandler(_llm_handler)
_llm_logger.propagate = False


def _call_llm(
    llm: ChatOllama,
    system: str,
    user: str,
    tools: List,
) -> List[Dict]:
    """Call Ollama via /api/generate, strip <think> tags & thinking field,
    parse tool calls from response text.
    qwen3.5 puts reasoning in a separate 'thinking' field and the actual
    output in 'response' — /api/chat strips thinking from content,
    leaving it empty. So we use /api/generate to get both fields."""
    model = getattr(llm, 'model', 'qwen3.5:9b')
    temperature = getattr(llm, 'temperature', 0.5)
    base = getattr(llm, 'base_url', None) or 'http://localhost:11434'
    url = f"{base.rstrip('/')}/api/generate"

    # Build a text description of available tools to inject into the prompt
    tools_text = ""
    for t in tools:
        schema = {"type": "object", "properties": {}, "required": []}
        if hasattr(t, 'args_schema') and t.args_schema:
            try:
                schema = t.args_schema.model_json_schema()
            except Exception:
                pass
        elif hasattr(t, 'args') and t.args:
            props = {}
            for k, v in t.args.items():
                if isinstance(v, dict) and "type" in v:
                    props[k] = {"type": v["type"]}
                    if v.get("description"):
                        props[k]["description"] = v["description"]
                    schema["required"].append(k)
            schema["properties"] = props
        tools_text += f"- {t.name}: {t.description}\n  Parameters: {json.dumps(schema)}\n\n"

    # Append tool instructions to the system prompt
    augmented_system = system + f"""

AVAILABLE FUNCTIONS:
{tools_text}

IMPORTANT: You MUST respond ONLY with a JSON object — no thinking, no analysis, no explanation, no markdown.
Output MUST start with '{' and end with '}' containing exactly one function call.

Format:
{{"function": "choose_segment", "arguments": {{"segment_id": "...", "reasoning": "...", "expected_delay_minutes": 0}}}}
"""

    augmented_user = user + """

Respond ONLY with the JSON object — no explanation, no thinking, no analysis. Just the JSON."""

    payload = {
        "model": model,
        "system": augmented_system,
        "prompt": augmented_user,
        "format": "json",
        "stream": False,
        "options": {"temperature": temperature},
    }

    try:
        resp = _requests.post(url, json=payload, timeout=None)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Ollama API error: {e}")
        _llm_logger.info(
            f"--- REQUEST ---\ntools_text:\n{tools_text}\nsystem:\n{system}\nuser:\n{user}\n"
            f"--- ERROR ---\n{e}"
        )
        return []

    data = resp.json()
    content = data.get("response", "")
    if not content:
        content = data.get("thinking", "")

    # Log raw response including <think> blocks + full response keys for debugging
    try:
        _llm_logger.info(
            f"--- REQUEST ---\ntools_text:\n{tools_text}\nsystem:\n{system}\nuser:\n{user}\n"
            f"--- FULL RESPONSE KEYS ---\n{list(data.keys())}\n"
            f"--- RAW RESPONSE ---\n{content}"
        )
    except Exception:
        pass

    # Strip reasoning tags
    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    cleaned = re.sub(r'<reasoning>.*?</reasoning>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<answer>.*?</answer>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'```json\s*|\s*```', '', cleaned)
    cleaned = cleaned.strip()

    tool_calls = []

    # Extract ALL JSON objects from cleaned text (handle nested braces)
    # Use a simple stack-based approach: find balanced { }
    i = 0
    while i < len(cleaned):
        if cleaned[i] != '{':
            i += 1
            continue
        depth = 0
        j = i
        while j < len(cleaned):
            if cleaned[j] == '{':
                depth += 1
            elif cleaned[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0:
            json_str = cleaned[i:j + 1]
            try:
                obj = json.loads(json_str)
                func_name = obj.get("function") or obj.get("name") or ""
                func_args = obj.get("arguments") or obj.get("parameters") or {}
                if func_name and isinstance(func_args, dict):
                    if "score" in func_args and "segment_id" not in func_args:
                        func_name = "evaluate_segment"
                    elif "segment_id" in func_args:
                        func_name = "choose_segment"
                    elif "progress_pct" in func_args:
                        func_name = "reflect"
                    tool_calls.append({"name": func_name, "args": func_args, "id": f"call_{len(tool_calls)}"})
            except json.JSONDecodeError:
                pass
            i = j + 1
        else:
            i += 1

    return tool_calls


# ─── State ──────────────────────────────────────────────────────────

class JourneyState(TypedDict):
    agent_id: str
    profile_text: str
    personality_self_intro: str
    personality_travel_plans: str
    personality_context: List[Tuple[str, str]]
    current_lat: float
    current_lon: float
    destination_lat: float
    destination_lon: float
    gold_shape: List[Tuple[float, float]]
    gold_length_km: float
    gold_duration_sec: float
    gold_maneuvers: List[Dict]
    maneuver_idx: int
    current_segment_begin: Tuple[float, float]
    current_segment_end: Tuple[float, float]
    current_alternatives: List[Dict]
    chosen_alternative: Optional[Dict]
    steps: List[Dict]
    step_count: int
    status: Literal["traveling", "arrived", "failed"]
    log: List[str]
    disruptions: List[Dict[str, Any]]
    active_disruptions_text: str
    congestion_zones: List[Dict[str, Any]]
    traffic_stream: Optional[str]
    route_edges: List[Dict[str, Any]]

# ─── Tools ──────────────────────────────────────────────────────────

@tool
def evaluate_segment(segment_id: str, score: int, reasoning: str) -> str:
    """Evaluate a single segment option. Score 1-10 based on your personal profile, risk tolerance, and current context."""
    return json.dumps({"segment_id": segment_id, "score": score, "reasoning": reasoning})

@tool
def choose_segment(segment_id: str, reasoning: str, expected_delay_minutes: int) -> str:
    """Choose the best segment for the next part of your journey."""
    return json.dumps({"segment_id": segment_id, "reasoning": reasoning, "delay": expected_delay_minutes})

@tool
def reflect(progress_pct: float, strategy_adjustment: str, confidence: int) -> str:
    """Reflect on your journey progress so far."""
    return json.dumps({"progress": progress_pct, "adjustment": strategy_adjustment, "confidence": confidence})

_tools = [choose_segment, reflect]

# ─── Helpers ────────────────────────────────────────────────────────

def _get_maneuver_point(maneuver: Dict, gold_shape: List[Tuple[float, float]], key: str) -> Tuple[float, float]:
    idx = maneuver.get(key, 0)
    if idx < len(gold_shape):
        return gold_shape[idx]
    return gold_shape[-1] if gold_shape else (0, 0)

def _format_segments(segments: List[Dict]) -> str:
    lines = []
    for s in segments:
        disruptions = s.get("disruptions_on_route", [])
        d_text = ""
        if disruptions:
            d_text = " | DISRUPTIONS: " + "; ".join(
                f"[{d['severity'].upper()}] {d['type']}"
                for d in disruptions[:3]
            )
        lines.append(
            f"  {s['id']}: {s['duration_minutes']}min {s['distance_km']}km "
            f"congestion={s['congestion_level']} roadblock={s.get('has_roadblocks', False)}"
            f"{d_text}"
        )
    return "\n".join(lines)

def _format_disruptions(disruptions: List[Dict]) -> str:
    if not disruptions:
        return "No disruptions nearby."
    return "\n".join(
        f"  [{d['severity'].upper()}] {d['type']}: {d.get('description','')} "
        f"({d['lat']:.4f}, {d['lon']:.4f}) radius={d['radius_meters']}m"
        for d in disruptions
    )

def _format_congestion(zones: List[Dict]) -> str:
    if not zones:
        return "No congestion zones nearby."
    return "\n".join(
        f"  [{z['severity'].upper()}] {z['n_agents']} agents near ({z['lat']:.4f}, {z['lon']:.4f}) radius={z['radius_meters']:.0f}m"
        for z in zones
    )

def _format_gold_instruction(maneuver: Dict) -> str:
    instruction = maneuver.get("instruction", "Proceed")
    streets = maneuver.get("street_names", [])
    street_str = ", ".join(streets) if streets else ""
    if street_str:
        return f"{instruction} ({street_str})"
    return instruction

def _describe_alternative(seg: Dict) -> str:
    streets = seg.get("description", "").replace("Via ", "")
    return streets[:80] if len(streets) > 80 else streets


def _check_disruptions_on_route(
    route_shape: List[Tuple[float, float]],
    disruptions: List[Dict[str, Any]],
    threshold_m: float = 500,
) -> List[Dict[str, Any]]:
    hits = []
    for d in disruptions:
        for pt in route_shape:
            dist = math.sqrt((pt[0] - d["lat"])**2 + (pt[1] - d["lon"])**2) * 111_000
            if dist < d.get("radius_meters", 500) + threshold_m:
                if d.get("affects_auto", True):
                    hits.append(d)
                    break
    return hits

# ─── Cache ──────────────────────────────────────────────────────────

_segment_cache: Dict[str, Any] = {}


def _merge_short_maneuvers(
    maneuvers: List[Dict],
    shape: List[Tuple[float, float]],
    min_distance_m: float = 250.0,
) -> List[Dict]:
    """Merge consecutive maneuvers where the distance between their end-points
    is less than *min_distance_m*.  This gives the LLM longer segments with
    genuinely different alternatives at each decision point."""
    if not maneuvers:
        return maneuvers

    merged = []
    current = dict(maneuvers[0])

    def _end_distance(m1, m2):
        i1 = m1.get("end_shape_index", 0)
        i2 = m2.get("end_shape_index", 0)
        if i1 >= len(shape) or i2 >= len(shape):
            return float("inf")
        dx = shape[i1][0] - shape[i2][0]
        dy = shape[i1][1] - shape[i2][1]
        return math.sqrt(dx * dx + dy * dy) * 111_000

    for m in maneuvers[1:]:
        dist = _end_distance(current, m)
        if dist < min_distance_m:
            current["end_shape_index"] = m.get("end_shape_index", current["end_shape_index"])
            street_names = list(dict.fromkeys(
                (current.get("street_names") or []) + (m.get("street_names") or [])
            ))
            if street_names:
                current["street_names"] = street_names
        else:
            merged.append(current)
            current = dict(m)

    merged.append(current)

    # Normalise begin_shape_index for the first point in each merged block
    shape_idx = 0
    for m in merged:
        m["begin_shape_index"] = shape_idx
        shape_idx = m["end_shape_index"]

    logger.info(
        f"Merged {len(maneuvers)} maneuvers → {len(merged)} decision points "
        f"(min_distance={min_distance_m:.0f}m)"
    )
    return merged

# ─── Nodes ──────────────────────────────────────────────────────────

def disruption_node(state: JourneyState) -> JourneyState:
    near = []
    for d in state["disruptions"]:
        dist = math.sqrt((state["current_lat"] - d["lat"])**2 +
                         (state["current_lon"] - d["lon"])**2) * 111_000
        if dist < d["radius_meters"] + 3000:
            near.append(d)
    state["active_disruptions_text"] = _format_disruptions(near)
    state["log"].append(f"[disruption] {len(near)} active disruptions nearby")
    return state

def query_maneuver_node(state: JourneyState, valhalla: ValhallaEngine) -> JourneyState:
    idx = state["maneuver_idx"]
    maneuvers = state["gold_maneuvers"]
    gold_shape = state["gold_shape"]

    if idx >= len(maneuvers):
        state["log"].append("[query] all maneuvers completed")
        return state

    maneuver = maneuvers[idx]
    end_point = _get_maneuver_point(maneuver, gold_shape, "end_shape_index")
    state["current_segment_begin"] = (state["current_lat"], state["current_lon"])
    state["current_segment_end"] = end_point

    cz = state.get("congestion_zones", [])
    cz_tuples = [(z["lat"], z["lon"], z["radius_meters"], z["n_agents"]) for z in cz] if cz else None
    traffic_stream = state.get("traffic_stream")
    current_pos = (state["current_lat"], state["current_lon"])

    routes = valhalla.alternatives(
        current_pos, end_point,
        n=5,
        congestion_zones=cz_tuples,
        traffic_stream=traffic_stream,
    )

    _segment_cache["routes"] = routes
    _segment_cache["end_point"] = end_point

    # Build alternatives with disruption info
    alt_list = []
    for r in routes:
        ctx = r.to_context()
        shape_pts = [(p.lat, p.lon) for p in r.shape]
        route_disruptions = _check_disruptions_on_route(shape_pts, state["disruptions"])
        ctx["disruptions_on_route"] = route_disruptions
        ctx["has_disruptions"] = len(route_disruptions) > 0
        ctx["street_names"] = _describe_alternative(ctx)
        alt_list.append(ctx)
    state["current_alternatives"] = alt_list

    route_edges = []
    for r in routes:
        for p in r.shape:
            if getattr(p, 'edge_id', None) and p.edge_id > 0:
                route_edges.append({
                    "edge_id": p.edge_id,
                    "speed": getattr(p, 'speed', 50) or 50,
                    "length_m": 100,
                })
    state["route_edges"] = route_edges

    # Log alternatives in detail
    instr = _format_gold_instruction(maneuver)
    state["log"].append(f"[query] maneuver {idx + 1}/{len(maneuvers)}: {instr}")
    for i, alt in enumerate(alt_list):
        d_flag = " ⚠DISRUPTION" if alt.get("has_disruptions") else ""
        state["log"].append(
            f"[query]   alt {i+1}: {alt['id']} {alt['duration_minutes']}min "
            f"{alt['distance_km']}km [{alt['congestion_level']}]{d_flag}"
        )
    return state

def agent_decision_node(state: JourneyState, llm: ChatOllama) -> JourneyState:
    maneuvers = state["gold_maneuvers"]
    idx = state["maneuver_idx"]
    maneuver = maneuvers[idx] if idx < len(maneuvers) else {}

    segments_str = _format_segments(state["current_alternatives"])
    disruptions_str = state.get("active_disruptions_text", "None")
    cz = state.get("congestion_zones", [])
    congestion_str = _format_congestion(cz) if cz else "No congestion zones nearby."

    total = len(maneuvers)
    progress_pct = (idx / total * 100) if total > 0 else 0

    gold_instruction = _format_gold_instruction(maneuver)
    eg = state["current_segment_end"]
    end_desc = f"({eg[0]:.4f}, {eg[1]:.4f})"

    context_lines = "\n".join(
        f"  At {loc}: {ctx}" for loc, ctx in state["personality_context"]
    ) if state.get("personality_context") else ""

    system = f"""You are {state['agent_id']}.

YOUR PERSONALITY:
{state['personality_self_intro']}

TODAY'S TRAVEL PLANS:
{state['personality_travel_plans']}

YOUR ITINERARY CONTEXT:
{context_lines}

You are navigating Chicago by choosing between alternative routes for each road segment.
At every intersection or street change, you evaluate which way to go.
Choose based on YOUR personal traits — NOT as a utility-maximizer.

Active disruptions:
{disruptions_str}

Traffic congestion:
{congestion_str}

IMPORTANT RULES:
- AVOID any route flagged with DISRUPTIONS — disruptions mean road closures, severe delays, or hazardous conditions
- A route with DISRUPTIONS should be your LAST choice, even if it is slightly faster
- Congested routes (congestion=moderate or higher) should be avoided when alternatives exist
- Your personality influences your choice: risk-tolerant agents may accept disruptions, cautious ones avoid them
- Slower routes without disruptions are often better than fast routes with delays/disruptions
- Choosing a different route than route_fastest is expected — that is why alternatives are provided"""

    user_clat = state["current_lat"]
    user_clon = state["current_lon"]
    user_dlat = state["destination_lat"]
    user_dlon = state["destination_lon"]
    user = f"""You are at ({user_clat:.4f}, {user_clon:.4f}).
Destination: ({user_dlat:.4f}, {user_dlon:.4f})
Progress: {progress_pct:.0f}% (step {idx + 1} of {total})

The next segment should get you toward: {end_desc}
Original direction: {gold_instruction}

Route options for this segment:
{segments_str}

Choose wisely based on your personality, disruptions, congestion, and travel preferences.

Evaluate all options internally, then output choose_segment with your final choice.
Do NOT call evaluate_segment — go directly to choose_segment."""

    if isinstance(llm, ChatOllama):
        tool_calls = _call_llm(llm, system, user, _tools)
    else:
        llm_tools = llm.bind_tools(_tools)
        response = llm_tools.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        tool_calls = getattr(response, "tool_calls", []) or []

    for tc in tool_calls:
        name = tc["name"]
        args = tc["args"]
        if name == "choose_segment" or name == "evaluate_segment":
            seg_id = args.get("segment_id")
            if not state.get("chosen_alternative") or name == "choose_segment":
                reason = args.get("reasoning", "")
                delay = args.get("expected_delay_minutes") or args.get("score", 0)
                state["chosen_alternative"] = {
                    "segment_id": seg_id,
                    "reasoning": reason,
                    "delay": delay if isinstance(delay, int) else 0,
                }
                state["log"].append(f"[agent] chose {seg_id}: {reason[:500]}")
        elif name == "reflect":
            state["log"].append(f"[agent] reflect: {args.get('progress_pct')}% confidence={args.get('confidence')}")

    # Fallback: if LLM returned nothing, empty segment, or a hallucinated
    # route ID that doesn't exist, pick the first available alternative
    chosen_id = (state.get("chosen_alternative") or {}).get("segment_id", "")
    valid_ids = {a["id"] for a in state.get("current_alternatives", [])}
    if (not chosen_id or chosen_id not in valid_ids) and state.get("current_alternatives"):
        first = state["current_alternatives"][0]
        reason = "(fallback) LLM produced no valid segment; chose first alternative."
        if chosen_id and chosen_id not in valid_ids:
            reason = f"(fallback) LLM hallucinated '{chosen_id}'; chose first alternative."
        state["chosen_alternative"] = {
            "segment_id": first["id"],
            "reasoning": reason,
            "delay": 0,
        }
        state["log"].append(reason)

    return state

def move_node(state: JourneyState) -> JourneyState:
    chosen_id = (state.get("chosen_alternative") or {}).get("segment_id")
    reasoning = (state.get("chosen_alternative") or {}).get("reasoning", "")
    routes = _segment_cache.get("routes", [])
    chosen = next((r for r in routes if r.route_id == chosen_id), None)

    # Record full shape of chosen route so map follows roads
    path_coords = []
    if chosen:
        path_coords = [(p.lat, p.lon) for p in chosen.shape]

    # Use the last point of the CHOSEN route as the next position,
    # NOT the gold maneuver end_point — this keeps position tracking
    # consistent with the actual route taken and prevents visual gaps.
    if path_coords:
        lat, lon = path_coords[-1]
    else:
        lat, lon = state["current_lat"], state["current_lon"]

    # Find disruptions on chosen route
    chosen_disruptions = []
    if path_coords:
        chosen_disruptions = _check_disruptions_on_route(path_coords, state["disruptions"])

    state["steps"].append({
        "index": state["step_count"],
        "from": [state["current_lat"], state["current_lon"]],
        "to": [lat, lon],
        "segment": chosen_id,
        "reasoning": reasoning,
        "maneuver": state["maneuver_idx"],
        "path_coords": path_coords,
        "disruptions_on_route": chosen_disruptions,
        "time": datetime.now().isoformat(),
    })
    state["step_count"] += 1
    state["current_lat"] = lat
    state["current_lon"] = lon
    state["maneuver_idx"] += 1
    state["log"].append(f"[move] maneuver {state['maneuver_idx']} via {chosen_id}")
    return state

def arrive_node(state: JourneyState) -> JourneyState:
    state["status"] = "arrived"
    state["log"].append(f"[arrive] DONE after {state['step_count']} steps, {state['maneuver_idx']} maneuvers")
    return state

def check_maneuvers(state: JourneyState) -> Literal["query", "arrive"]:
    idx = state["maneuver_idx"]
    total = len(state["gold_maneuvers"])
    if idx >= total:
        state["log"].append(f"[check] all {total} maneuvers done")
        return "arrive"
    if state["step_count"] >= 100:
        return "arrive"
    state["log"].append(f"[check] maneuver {idx + 1}/{total}")
    return "query"

# ─── Build & Run ───────────────────────────────────────────────────

def build_graph(valhalla: ValhallaEngine, llm: ChatOllama):
    graph = StateGraph(JourneyState)

    graph.add_node("disruption_check", disruption_node)
    graph.add_node("query_maneuver", lambda s: query_maneuver_node(s, valhalla))
    graph.add_node("agent_decision", lambda s: agent_decision_node(s, llm))
    graph.add_node("move", move_node)
    graph.add_node("arrive", arrive_node)

    graph.add_edge(START, "disruption_check")
    graph.add_edge("disruption_check", "query_maneuver")
    graph.add_edge("query_maneuver", "agent_decision")
    graph.add_edge("agent_decision", "move")
    graph.add_conditional_edges("move", check_maneuvers, {"query": "query_maneuver", "arrive": "arrive"})
    graph.add_edge("arrive", END)

    return graph.compile()


def run(agent_id: str, profile_text: str,
        origin: Tuple[float, float], destination: Tuple[float, float],
        gold_waypoints: List[Tuple[float, float]], gold_length_km: float = 0, gold_duration_sec: float = 0,
        valhalla: Optional[ValhallaEngine] = None, llm: Optional[ChatOllama] = None,
        recursion_limit: int = 50,
        disruptions: Optional[List[Dict[str, Any]]] = None,
        congestion_zones: Optional[List[Dict[str, Any]]] = None,
        traffic_stream: Optional[str] = None,
        personality_self_intro: str = "",
        personality_travel_plans: str = "",
        personality_context: Optional[List[Tuple[str, str]]] = None) -> Dict:

    v = valhalla or ValhallaEngine()
    gold_route = v.route(origin, destination)

    if gold_route and gold_route.maneuvers:
        gold_maneuvers = gold_route.maneuvers
        gold_shape = [(p.lat, p.lon) for p in gold_route.shape]
        length = gold_route.length_km
        duration = gold_route.duration_seconds
    else:
        gold_shape = [(p.lat, p.lon) for p in gold_route.shape] if gold_route else [destination]
        gold_maneuvers = [{
            "instruction": "Proceed to destination",
            "begin_shape_index": 0,
            "end_shape_index": len(gold_shape) - 1,
            "street_names": [],
        }]
        length = gold_length_km or (gold_route.length_km if gold_route else 0)
        duration = gold_duration_sec or (gold_route.duration_seconds if gold_route else 0)

    real_maneuvers = [m for m in gold_maneuvers
                      if m.get("begin_shape_index", 0) < m.get("end_shape_index", 0)
                      and "arrived" not in m.get("instruction", "").lower()]

    # Merge short consecutive maneuvers so alternatives are diverse
    real_maneuvers = _merge_short_maneuvers(real_maneuvers, gold_shape, min_distance_m=250.0)

    logger.info(f"[{agent_id}] {len(real_maneuvers)} maneuvers from {len(gold_shape)} shape points")

    state: JourneyState = {
        "agent_id": agent_id, "profile_text": profile_text,
        "current_lat": origin[0], "current_lon": origin[1],
        "destination_lat": destination[0], "destination_lon": destination[1],
        "gold_shape": gold_shape,
        "gold_length_km": length,
        "gold_duration_sec": duration,
        "gold_maneuvers": real_maneuvers,
        "maneuver_idx": 0,
        "current_segment_begin": (0, 0),
        "current_segment_end": (0, 0),
        "current_alternatives": [],
        "chosen_alternative": None,
        "steps": [], "step_count": 0,
        "status": "traveling", "log": [],
        "disruptions": disruptions or [],
        "active_disruptions_text": "",
        "congestion_zones": congestion_zones or [],
        "traffic_stream": traffic_stream,
        "route_edges": [],
        "personality_self_intro": personality_self_intro or profile_text,
        "personality_travel_plans": personality_travel_plans or "",
        "personality_context": personality_context or [],
    }

    app = build_graph(v, llm or ChatOllama(model="llama3.2", temperature=0.5))
    return app.invoke(state, {"recursion_limit": recursion_limit})
