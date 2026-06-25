import instructor
from openai import OpenAI
from pydantic import BaseModel, Field, model_validator
from typing import Any, List, Literal


TIMEOUT = 120.0   # seconds before an ollama call is killed
MAX_RETRIES = 3   # instructor formatting retries per call

client = instructor.from_openai(
    OpenAI(base_url="http://localhost:11434/v1", api_key="ollama", timeout=TIMEOUT),
    mode=instructor.Mode.JSON
)

MODEL = "llama3.1:8b"


# ── Base schema ────────────────────────────────────────────────────────────────

class BaseSchema(BaseModel):
    """Unwraps class-named JSON envelopes produced by small models.

    llama3.1:8b tends to output {"ClassName": {fields}} instead of {fields}.
    The before-validator strips that outer key so Pydantic sees the flat dict.
    """
    @model_validator(mode="before")
    @classmethod
    def unwrap_envelope(cls, data: Any) -> Any:
        if isinstance(data, dict) and len(data) == 1:
            (key, value) = next(iter(data.items()))
            if isinstance(value, dict):
                return value
        return data


# ── Schemas ────────────────────────────────────────────────────────────────────

class PersonaProfile(BaseSchema):
    """Structured persona constructed from demographic data, written in second person."""
    name: str = Field(description="Realistic first and last name consistent with the person's background.")
    age: int = Field(description="Age of the person in years.")
    description: str = Field(
        description=(
            "Second-person persona description ready to inject as roleplay context. "
            "Covers who the person is, where they live, what they do for work, "
            "their hobbies, and key personality traits. "
            "Example: 'You are Maria Gonzalez, a 59-year-old elementary school "
            "teacher living in a quiet suburban neighbourhood outside Chicago. "
            "You have worked at the same school for over twenty years and spend "
            "your evenings gardening and attending your church choir.'"
        )
    )


class PreviousDayContext(BaseSchema):
    """Second-person previous-day context ready to inject into a roleplaying agent's system prompt."""
    context: str = Field(
        description=(
            "Second-person summary of what the persona experienced the day before. "
            "Covers mood, notable events, and any carry-over feelings or intentions. "
            "Example: 'Yesterday was a long day — your last class ran over and you "
            "stayed late to help a student. You stopped by the grocery store on the "
            "way home and cooked dinner for your husband. You went to bed a little "
            "tired but feeling good about the week.'"
        )
    )


class PersonaWithContext(BaseSchema):
    """Complete output of PersonaAgent bundling the structured profile and previous-day context."""
    profile: PersonaProfile = Field(description="Structured persona profile.")
    previous_day: PreviousDayContext = Field(description="Previous-day context.")


Location = Literal["HOME", "WORK", "SCHOOL", "DISCRETIONARY"]


class DailyPlan(BaseSchema):
    """The roleplaying agent's deliberated daily plan."""
    self_introduction: str = Field(
        description=(
            "A short first-person passage where the agent introduces themselves in character — "
            "who they are, what matters to them, and how they approach their day."
        )
    )
    travel_plans_summary: str = Field(
        description="A brief first-person message describing the agent's travel plans for the day."
    )
    locations: List[Location] = Field(
        description=(
            "Ordered list of locations the agent plans to visit. "
            "Must begin with 'HOME'. "
            "Valid values: HOME, WORK, SCHOOL, DISCRETIONARY."
        )
    )
    location_context: List[str] = Field(
        description=(
            "Parallel list of first-person motivations for each location — one entry per location. "
            "Example for DISCRETIONARY: 'I ran out of tomatoes, I should stop by the store after work.'"
        )
    )
    departure_times: List[str] = Field(
        description=(
            "Parallel list of intended departure times — one entry per location. "
            "Use 12-hour format, e.g. '7:00 AM', '5:30 PM'."
        )
    )

    @model_validator(mode="after")
    def lists_same_length(self) -> "DailyPlan":
        n = len(self.locations)
        if len(self.location_context) != n or len(self.departure_times) != n:
            raise ValueError(
                "locations, location_context, and departure_times must all have the same length."
            )
        if self.locations[0] != "HOME":
            raise ValueError("The first location must be 'HOME'.")
        return self


# ── System prompts ─────────────────────────────────────────────────────────────

_PERSONA_SYSTEM_PROMPT = """You are an expert social scientist who builds realistic synthetic human personas from census demographic data.
Your output will be used as the system-prompt context for a separate roleplaying agent, so the persona description must be written in second person.

Steps:
- Read all provided socio-demographic attributes carefully.
- Infer a realistic name, occupation, home location type, and personal history consistent with the demographics.
- Identify 2-3 hobbies or interests that fit the person's age, income, and lifestyle.
- Compose the description as a second-person character brief: who they are, where they live, what they do, and what they care about.

Output instructions:
- The description field must be written in second person ('You are...', 'You live...', 'You enjoy...').
- Use concrete, specific details — avoid vague generalities or demographic stereotypes.
- Keep the description to 3-5 sentences: enough to anchor a roleplaying agent without over-constraining it.
- Every detail must be internally consistent with the provided demographic data."""

_PREVIOUS_DAY_SYSTEM_PROMPT = """You generate contextual backstory for synthetic personas used in travel-diary simulations.
Your output will be injected into a roleplaying agent's system prompt, so it must be written in second person.

Steps:
- Review the persona description provided.
- Imagine a plausible previous day: what errands they ran, how work went, who they interacted with, how they felt.
- Highlight anything that might colour their mood or plans for the upcoming day.

Output instructions:
- Write in second person ('Yesterday you...', 'You felt...', 'You ended up...').
- Write in flowing prose — no bullet points or lists.
- Keep it to 3-5 sentences: enough to set a mood and hint at carry-over intentions without being exhaustive.
- The day should feel ordinary and typical for this person."""

_ROLEPLAY_SYSTEM_PROMPT = """You are a synthetic person participating in a travel diary study.
You will be given a description of who you are and context about your previous day.
Fully inhabit this role — think, speak, and plan as this person would.

Your day always begins at HOME before going anywhere else.
locations must only contain: HOME, WORK, SCHOOL, DISCRETIONARY.
location_context must have exactly one first-person motivation per location.
departure_times must have exactly one time per location in 12-hour format (e.g. '7:00 AM').
All three lists must be the same length.
Write self_introduction and travel_plans_summary in first person, fully in character."""


# ── Agents ─────────────────────────────────────────────────────────────────────

class PersonaAgent:
    """
    Generates a PersonaWithContext from raw census demographic attributes.

    Step 1: builds a PersonaProfile (name, age, second-person description).
    Step 2: generates a PreviousDayContext seeding behavioural state for the roleplaying agent.

    Both steps call instructor directly with the appropriate response_model so structured
    schemas are returned rather than BasicChatOutputSchema.
    """

    def run(self, demographic_description: str) -> PersonaWithContext:
        profile: PersonaProfile = client.chat.completions.create(
            model=MODEL,
            max_retries=MAX_RETRIES,
            messages=[
                {"role": "system", "content": _PERSONA_SYSTEM_PROMPT},
                {"role": "user", "content": demographic_description},
            ],
            response_model=PersonaProfile,
        )
        previous_day: PreviousDayContext = client.chat.completions.create(
            model=MODEL,
            max_retries=MAX_RETRIES,
            messages=[
                {"role": "system", "content": _PREVIOUS_DAY_SYSTEM_PROMPT},
                {"role": "user", "content": profile.description},
            ],
            response_model=PreviousDayContext,
        )
        return PersonaWithContext(profile=profile, previous_day=previous_day)


class RoleplayingAgent:
    """
    Inhabits the persona produced by PersonaAgent and deliberates a daily travel plan.

    Input:  PersonaWithContext (from PersonaAgent.run())
    Output: DailyPlan with self-introduction, travel summary, and parallel
            lists of locations, motivations, and departure times.
    """

    def run(self, persona: PersonaWithContext) -> DailyPlan:
        return client.chat.completions.create(
            model=MODEL,
            max_retries=MAX_RETRIES,
            messages=[
                {"role": "system", "content": _ROLEPLAY_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"{persona.profile.description}\n\n{persona.previous_day.context}"
                )},
            ],
            response_model=DailyPlan,
        )
