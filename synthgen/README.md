# synthgen

Generates synthetic human agents from US census (PUMS) demographic data. Each agent gets a
demographic profile, a persona narrative, a first-person introduction, and a one-day travel
diary. The pipeline is driven by a local Ollama LLM and orchestrated by [`main.py`](main.py).

---

## Pipeline overview

`main.py` runs a four-stage pipeline. Each stage writes an intermediate file to the run folder,
so stages can be re-run independently and are skipped automatically if their output already exists.

```
                 ┌─────────────────────────────────────────────────────────────┐
                 │  1. population   → population_descriptions.json, population.csv │
                 │  2. narratives   → narratives.json                             │
                 │  3. intros       → intros.json                                 │
                 │  4. diaries      → diaries.json                                │
                 └─────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
                              combine → agents.jsonl   (final output)
```

| Stage | Module | Produces | Prompt used |
|-------|--------|----------|-------------|
| 1. Population | [`population.py`](population.py) | `population_descriptions.json`, `population.csv` | — (no LLM; demographic templating) |
| 2. Narratives | [`narratives.py`](narratives.py) | `narratives.json` | `narrative` |
| 3. Intros | [`intros.py`](intros.py) | `intros.json` | `intro` |
| 4. Diaries | [`diaries.py`](diaries.py) | `diaries.json` | `diary` |
| Combine | [`main.py`](main.py) | `agents.jsonl` | — |

---

## Setup

1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Install and run [Ollama](https://ollama.com/) locally, and pull the model referenced in
   `config.yaml` (`ollama.model`):
   ```bash
   ollama pull <model-name>
   ```
3. Make sure the census / travel data is in place. The data directory is set by
   `data.base_path` in `config.yaml` (default `./data`). To fetch the MyDailyTravel source
   data, run:
   ```bash
   ./setup_mydailytravel.sh
   ```

---

## Running the pipeline

```bash
python main.py <config_yaml> <run_path> [flags]
```

- `config_yaml` — path to a config file (e.g. [`config.yaml`](config.yaml)).
- `run_path` — folder where all intermediate files and the final `agents.jsonl` are written
  (created if it doesn't exist).

**Run everything (auto-detect what's missing):**
```bash
python main.py config.yaml ./run
```
With no stage flags, `main.py` runs whichever stages have missing output files, then combines
the results. Re-running is safe — completed stages are skipped.

**Run specific stages:**
```bash
python main.py config.yaml ./run --generate-population
python main.py config.yaml ./run --generate-narratives
python main.py config.yaml ./run --generate-diaries
python main.py config.yaml ./run --create-incidents
```

| Flag | Effect |
|------|--------|
| `--generate-population` | Synthesize the population sample and demographic descriptions. |
| `--generate-narratives` | Generate persona narratives (needs population first). |
| `--generate-diaries` | Generate intros **and** travel diaries (needs narratives first). |
| `--create-incidents` | Build a traffic-incident file from `Traffic_*Crashes_*.csv` in the data dir. |
| `--verbose` | Print progress and previews of generated content. |
| `--debug` | Include the exact formatted prompt sent to the LLM in each output record (`debug_*_prompt`). |

---

## Output: `agents.jsonl`

The final combined file is **JSON Lines** — one JSON object per line. Each record:

```jsonc
{
  "agent_idx": 0,
  "description": "...",                 // demographic profile text
  "travel_plans_summary": "...",        // persona narrative (2nd person)
  "mood": "neutral",
  "self_introduction": "...",           // first-person intro
  "itinerary": {
    "locations": ["HOME", "WORK", "HOME"],
    "location_context": ["...", "...", "..."],
    "departure_times": ["08:00", "17:30"]
  }
}
```

`agent_idx` is the join key — use it to map back to `population.csv` for demographic columns
(`SERIALNO`, `PUMA`, etc.). With `--debug`, records also carry `debug_narrative_prompt`,
`debug_intro_prompt`, and `debug_diary_prompt`. If `demographic_inclusion` is `footer`/`both`,
a `_sociodemographic_data` block is appended.

Combining checkpoints every N agents is controlled by `agents.checkpoint_frequency` in the
config (set to `0` to disable).

---

## Configuration (`config.yaml`)

| Section | Key | Meaning |
|---------|-----|---------|
| `ollama` | `model`, `temperature`, `top_p`, `num_predict` | LLM model and sampling params. |
| | `timeout_seconds`, `max_retries` | Per-call timeout and retry count (see [`timeout_retry.py`](timeout_retry.py)). |
| `agents` | `n_sample` | Number of agents to generate. |
| | `demographic_inclusion` | How demographics enter narratives: `narrative` \| `footer` \| `both`. |
| | `demographic_inclusion_percent` | Fraction of demographic attributes to weave into narrative text. |
| | `demographic_inclusion_in_intros_diaries` | Demographic context for intros/diaries: `none` \| `narrative` \| `footer` \| `both`. |
| | `checkpoint_frequency` | Cache combined output every N agents (`0` disables). |
| `synth` | `source`, `sim_year` | Population source region and simulation year. |
| `data` | `base_path`, `mydailytravel_source_path` | Input data locations. |
| `survey` | `min_age`, `max_age` | Age filter for the population sample. |
| `mood` | `<name>: <weight>` | Mood distribution sampled per agent (e.g. `neutral: 1.0`). |

---

## Modifying the prompts (`prompts.yaml`)

All LLM prompts live in [`prompts.yaml`](prompts.yaml) as named templates. Edit the text there —
no code changes needed. Placeholders use `{name}` and are filled at runtime with
[LangChain `PromptTemplate`](https://python.langchain.com/). To emit a **literal** brace in a
template (as the `diary` prompt does for its JSON example), double it: `{{` / `}}`.

### `narrative` — persona narrative

- **Purpose:** Turns a raw demographic profile into a 5–7 sentence second-person character
  narrative. It infers a name, occupation, history, and a "yesterday" that colors today's mood.
  This is the core persona that seeds all downstream generation.
- **Output field:** `travel_plans_summary` in `agents.jsonl` (stored as `narrative` in
  `narratives.json`).
- **Placeholders:**
  - `{description}` — the demographic profile text.
  - `{mood}` — the mood sampled for this agent (from the `mood` config distribution).
  - `{demographic_instruction}` — auto-generated instruction injected by
    [`narratives.py`](narratives.py) when `demographic_inclusion` is `narrative`/`both`,
    telling the model to weave in `demographic_inclusion_percent` of the attributes.
- **Notes:** Must be written in second person ("Yesterday you…"). Keep the length guidance if
  you want concise personas.

### `intro` — first-person self-introduction

- **Purpose:** A short (2–3 sentence) first-person introduction that establishes the persona's
  voice and hints at lifestyle/values relevant to travel behavior.
- **Output field:** `self_introduction` in `agents.jsonl` (stored as `intro` in `intros.json`).
- **Placeholders:**
  - `{description}` — demographic profile. **Conditionally stripped:** if
    `demographic_inclusion_in_intros_diaries` is `none`/`footer`, [`intros.py`](intros.py)
    removes the `Demographic profile: {description}` line entirely.
  - `{narrative}` — the persona narrative from stage 2.

### `diary` — one-day travel diary

- **Purpose:** Produces the agent's daily itinerary as strict JSON: locations, per-location
  context, and departure times. Must start and end at `HOME`.
- **Output field:** `itinerary` object in `agents.jsonl` (stored in `diaries.json`).
- **Placeholders:**
  - `{description}` — demographic profile (conditionally stripped, same rule as `intro`).
  - `{narrative}` — persona narrative.
  - `{intro}` — the self-introduction from stage 3.
- **Structural rules (enforced/validated in [`diaries.py`](diaries.py)):** locations start with
  `HOME` and use only `HOME`/`WORK`/`SCHOOL`/`DISCRETIONARY`; `contexts` length matches
  `locations`; `departures` has one fewer entry than `locations`; times are `HH:MM` between
  06:00–23:00, ≥1 hour apart; no back-to-back repeats; ≥3 locations.
  ⚠️ If you change the JSON keys or the rules in this prompt, update the validation and parsing
  in `diaries.py` to match, or generations will be rejected.

### Tips for editing prompts

- **Keep placeholder names intact** — removing a `{name}` the code passes to `.format()` will
  raise a `KeyError`; adding a new placeholder requires wiring it up in the corresponding module.
- Use `--debug` to dump the fully-rendered prompt into each output record so you can see exactly
  what the model received.
- Use `--verbose` with a small `n_sample` (e.g. `3`) to iterate quickly on wording.
