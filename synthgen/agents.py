import instructor
from openai import OpenAI
from pydantic import Field
from atomic_agents.context import ChatHistory
from atomic_agents.context.system_prompt_generator import SystemPromptGenerator
from atomic_agents import AtomicAgent, AgentConfig, BaseIOSchema


client = instructor.from_openai(
    OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON
)

MODEL = "gemma4:e4b"

# ----------------------------
# PERSONA AGENT
# ----------------------------

def build_persona_system_prompt():
    return SystemPromptGenerator(
        background=[
            "You create detailed fictional personas for role-playing agents.",
            "You are given structured sociodemographic attributes for one individual.",
            "Your task is to transform those attributes into a believable, realistic character profile grounded in the provided data.",
            "Use the input facts as constraints: the persona should be consistent with them and should not contradict them.",
            "You may make reasonable, low-risk inferences about personality, motivations, habits, and communication style, but these should remain plausible and nuanced.",
            "Avoid stereotypes, caricatures, or assumptions that are not reasonably supported by the provided sociodemographic data.",
            "If some fields are missing, vague, or unspecified, fill gaps conservatively and naturally without mentioning that the data was missing."
        ],
        steps=[
            "Read the provided sociodemographic data carefully and extract the core facts about the individual.",
            "Construct one fictional individual who matches those facts exactly where they are specified.",
            "Develop a compact but vivid persona that includes likely life circumstances, temperament, values, motivations, social style, and personal challenges.",
            "Infer details in a restrained way: add only information that plausibly fits the provided profile and does not conflict with any stated attribute.",
            "Describe the person in a way that would help another agent consistently role-play them in conversation.",
            "Keep the character internally consistent, realistic, and psychologically coherent.",
            "Do not repeat the raw attribute list verbatim; instead, synthesize it into natural prose."
        ],
        output_instructions=[
            "Return a single prose passage.",
            "Write in clear natural language, not bullet points.",
            "The passage should read as a first person description of an individual.",
            "Do not include commentary, notes, or explanations outside the description.",
            "Output only content appropriate for the persona_description field."
        ]
    )


class PersonaInputSchema(BaseIOSchema):
    """Multiline sociodemographic key-value attributes for one individual."""
    sociodemographic_data: str = Field(
        ...,
        description="Multiline sociodemographic key-value attributes for one individual."
    )


class PersonaOutputSchema(BaseIOSchema):
    """A single prose passage to be used as a persona for a role-playing agent."""
    persona_description: str = Field(
        ...,
        description="A single prose passage to be used as a persona for a role-playing agent."
    )


def build_persona_agent():
    return AtomicAgent[PersonaInputSchema, PersonaOutputSchema](
        config=AgentConfig(
            client=client,
            model=MODEL,
            history=ChatHistory(),
            model_api_parameters=None,
            system_prompt_generator=build_persona_system_prompt(),
        )
    )


# ----------------------------
# SCHEDULE AGENT
# ----------------------------

def build_schedule_system_prompt():
    return SystemPromptGenerator(
        background=[
            "You generate realistic daily activity schedules for a role-playing travel behavior agent.",
            "You are given a first-person persona description and a day context.",
            "Your task is to infer a plausible typical daily sequence of activity locations.",
            "Allowed activity labels are only: HOME, SCHOOL, WORK, DISCRETIONARY.",
            "The schedule must begin at HOME.",
            "The schedule should be behaviorally realistic and consistent with the persona.",
            "You must also provide a brief first-person thought that reflects the person's reasoning or attitude toward the day.",
            "The thought should sound like a natural internal reaction and should help explain the schedule choice.",
            "The thought must not mention prompts, schemas, orchestration, simulation, or being an AI."
        ],
        steps=[
            "Read the persona carefully and infer the person's likely obligations and daily routine.",
            "Determine a plausible day pattern consistent with the persona and day context.",
            "Construct an ordered list of activities using only the allowed labels.",
            "Ensure the first activity is HOME.",
            "End the schedule in the most plausible location for a typical day, often HOME.",
            "Write a short first-person thought that reflects how the person sees or approaches the day."
        ],
        output_instructions=[
            "Return structured output only.",
            "The activities field must be an ordered list of activity labels.",
            "Use only HOME, SCHOOL, WORK, DISCRETIONARY.",
            "The thought field must be short, first-person, and natural.",
            "Do not include extra commentary."
        ]
    )


class ScheduleInputSchema(BaseIOSchema):
    """Persona description and day context"""
    persona_description: str = Field(...)
    day_context: str = Field(...)


class ScheduleOutputSchema(BaseIOSchema):
    """Activities list and though context"""
    activities: list[str] = Field(...)
    thought: str = Field(...)


def build_schedule_agent():
    return AtomicAgent[ScheduleInputSchema, ScheduleOutputSchema](
        config=AgentConfig(
            client=client,
            model=MODEL,
            history=ChatHistory(),
            model_api_parameters=None,
            system_prompt_generator=build_schedule_system_prompt(),
        )
    )


# ----------------------------
# TRAVEL DECISION AGENT
# ----------------------------

def build_travel_decision_system_prompt():
    return SystemPromptGenerator(
        background=[
            "You are a travel decision agent role-playing a person making realistic transportation choices.",
            "You are given a first-person persona description, a planned activity schedule, current trip context, and a travel status update.",
            "Your task is to decide whether the person proceeds as planned, reroutes, deviates, or abandons the trip.",
            "Your response must reflect the person's likely behavior and preferences.",
            "You must produce a structured trip message that includes origin, destination, travel mode, and things to avoid.",
            "You must also provide a brief first-person thought expressing the person's immediate reaction.",
            "The thought should be natural and human-sounding.",
            "The thought must not mention prompts, schemas, orchestration, simulation, tools, or being an AI."
        ],
        steps=[
            "Read the persona and current trip context carefully.",
            "Interpret the status update and determine how it affects the person's plans.",
            "Decide whether the traveler should proceed, reroute, deviate, or abandon the trip.",
            "Choose a plausible travel mode consistent with the persona and situation.",
            "List any routing or travel preferences to avoid, such as highways, congestion, tolls, closures, or delays.",
            "Write a short first-person thought that captures the person's reaction to the situation."
        ],
        output_instructions=[
            "Return structured output only.",
            "The decision field must be one of: proceed, reroute, deviate, abandon.",
            "The avoid field should contain short routing preferences or obstacles to avoid.",
            "The thought field must be brief, first-person, and natural.",
            "Do not include extra explanation."
        ]
    )


class TravelDecisionInputSchema(BaseIOSchema):
    """Travel decision and locations"""
    persona_description: str = Field(...)
    schedule: list[str] = Field(...)
    current_location: str = Field(...)
    next_destination: str = Field(...)
    status_message: str = Field(...)


class TripMessageSchema(BaseIOSchema):
    """Trip execution"""
    from_address: str = Field(...)
    to_address: str = Field(...)
    mode: str = Field(...)
    avoid: list[str] = Field(default_factory=list)
    decision: str = Field(...)
    thought: str = Field(...)


def build_travel_decision_agent():
    return AtomicAgent[TravelDecisionInputSchema, TripMessageSchema](
        config=AgentConfig(
            client=client,
            model=MODEL,
            history=ChatHistory(),
            model_api_parameters=None,
            system_prompt_generator=build_travel_decision_system_prompt(),
        )
    )


# ----------------------------
# SIMULATOR CLASS
# ----------------------------

class TravelDaySimulator:
    def __init__(self):
        self.persona_agent = build_persona_agent()
        self.schedule_agent = build_schedule_agent()
        self.travel_agent = build_travel_decision_agent()

        self.persona_description = None
        self.original_schedule = []
        self.schedule = []
        self.schedule_thought = None

        self.current_leg_index = 0
        self.completed_legs = []
        self.decision_log = []
        self.schedule_changed = False
        self.finished = False

    def initialize(self, sociodemographic_data: str, day_context: str = "typical weekday"):
        persona_result = self.persona_agent.run(
            PersonaInputSchema(sociodemographic_data=sociodemographic_data)
        )
        self.persona_description = persona_result.persona_description

        schedule_result = self.schedule_agent.run(
            ScheduleInputSchema(
                persona_description=self.persona_description,
                day_context=day_context
            )
        )
        self.original_schedule = list(schedule_result.activities)
        self.schedule = list(schedule_result.activities)
        self.schedule_thought = schedule_result.thought

        self.current_leg_index = 0
        self.completed_legs = []
        self.decision_log = []
        self.schedule_changed = False
        self.finished = len(self.schedule) < 2

    def get_current_leg(self):
        if self.finished or self.current_leg_index >= len(self.schedule) - 1:
            return None

        return {
            "from": self.schedule[self.current_leg_index],
            "to": self.schedule[self.current_leg_index + 1]
        }

    def step(self, status_message: str):
        if self.finished:
            return None

        leg = self.get_current_leg()
        if leg is None:
            self.finished = True
            return None

        travel_input = TravelDecisionInputSchema(
            persona_description=self.persona_description,
            schedule=self.schedule,
            current_location=leg["from"],
            next_destination=leg["to"],
            status_message=status_message
        )

        result = self.travel_agent.run(travel_input)

        record = {
            "leg_index": self.current_leg_index,
            "from": result.from_address,
            "to": result.to_address,
            "mode": result.mode,
            "avoid": result.avoid,
            "decision": result.decision,
            "thought": result.thought,
            "status_message": status_message
        }
        self.decision_log.append(record)

        if result.decision in ("proceed", "reroute"):
            self.completed_legs.append(record)
            self.current_leg_index += 1

        elif result.decision == "deviate":
            self.schedule[self.current_leg_index + 1] = "DISCRETIONARY"
            self.schedule_changed = True
            self.completed_legs.append(record)
            self.current_leg_index += 1

        elif result.decision == "abandon":
            self.schedule = self.schedule[:self.current_leg_index + 1] + ["HOME"]
            self.schedule_changed = True

            if self.schedule[self.current_leg_index] == "HOME":
                self.finished = True

        if self.current_leg_index >= len(self.schedule) - 1:
            self.finished = True

        return record

    def run_day(self, status_messages: list[str]):
        outputs = []
        for msg in status_messages:
            if self.finished:
                break
            outputs.append(self.step(msg))
        return outputs

    def get_state(self):
        lines = [
            "=== Travel Day Simulator State ===",
            f"Persona: {self.persona_description}",
            f"Original schedule: {self.original_schedule}",
            f"Current schedule: {self.schedule}",
            f"Schedule thought: {self.schedule_thought}",
            f"Current leg index: {self.current_leg_index}",
            f"Current leg: {self.get_current_leg()}",
            f"Schedule changed: {self.schedule_changed}",
            f"Finished: {self.finished}",
            "",
            "Completed legs:"
        ]

        if self.completed_legs:
            for i, leg in enumerate(self.completed_legs, 1):
                lines.append(
                    f"  {i}. {leg['from']} -> {leg['to']} | "
                    f"mode={leg['mode']} | decision={leg['decision']} | "
                    f"avoid={leg['avoid']} | thought={leg['thought']}"
                )
        else:
            lines.append("  None")

        lines.append("")
        lines.append("Decision log:")

        if self.decision_log:
            for i, decision in enumerate(self.decision_log, 1):
                lines.append(
                    f"  {i}. status='{decision['status_message']}' | "
                    f"{decision['from']} -> {decision['to']} | "
                    f"decision={decision['decision']} | mode={decision['mode']} | "
                    f"avoid={decision['avoid']} | thought={decision['thought']}"
                )
        else:
            lines.append("  None")

        return "\n".join(lines)


# ----------------------------
# EXAMPLE USAGE
# ----------------------------

if __name__ == "__main__":
    simulator = TravelDaySimulator()

    simulator.initialize(
        sociodemographic_data="""
Your name is Alexandra, a 24-year old ambitious and hardworking woman who just graduated with a bachelor's degree from the University of Illinois. You work as an employee at a non-profit organization in Chicago, where you're learning the ropes and contributing to a cause you believe in. Growing up in Illinois, you're proud of your Midwestern roots and values, and you enjoy trying out new restaurants and cuisines with friends on your days off. Despite being busy, you prioritize self-care and aim to stay active through yoga and long walks along Lake Michigan. Your future plans include pursuing a master's degree and advancing in your career to make a meaningful impact.
""",
        day_context="typical weekday"
    )

    print("PERSONA:")
    print(simulator.persona_description)
    print()

    print("INITIAL SCHEDULE:")
    print(simulator.schedule)
    print("SCHEDULE THOUGHT:")
    print(simulator.schedule_thought)
    print()

    disruptions = [
        "Traffic on the route from home to work is much heavier than normal.",
        "A delay is reported near the discretionary destination.",
        "Conditions are normal for the return trip home."
    ]

    outputs = simulator.run_day(disruptions)

    print("TRIP DECISIONS:")
    for item in outputs:
        print(item)
    print()

    print("FINAL STATE:")
    print(simulator.get_state())