from preprocess import *
from langroid.language_models import LLMMessage
from synthesize import SurveyAgent, _ladaragTool
from typing import Tuple, Union, Dict, List
from datetime import datetime
from dataclasses import dataclass
import requests
import re
import json

@dataclass
class AgentResponsePackage:
    agent_id: str
    agent_bio: str
    serial_number: str
    logic_flow: List[str]
    parsed_responses: List[str | int | List[int]]
    responses_scraps: List[str]
    encoded_responses: int | str
    tool_dtypes: List[str]
    dtype_matches: List[bool]
    n_questions: int
    bad_iteration: bool

def _strip_think_blocks(text: str) -> str:
    """
    Remove <think>...</think> reasoning blocks from reasoning models.
    """
    return re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    ).strip()


def _response_from_tool_message(survey_response) -> Tuple[Union[Dict[str, str], None], Union[str, None]]:
    """
    Extracts JSON content from the message and returns it along with any extra text.

    Args:
        survey_response (LLMMessage): langroid LLMMessage implementation

    Returns:
        Tuple[Dict[str, str] | None, str | None]: JSON dict and any leftover text.
    """
    message_content = survey_response.content

    # remove reasoning blocks
    message_content = _strip_think_blocks(message_content)
    match = re.search(r'{.*}', message_content, re.DOTALL)

    if match:
        json_str = match.group(0)

        try:
            tool_content = json.loads(json_str)
        except json.JSONDecodeError:
            return None, message_content.strip()

        start, end = match.span()
        scraps = (message_content[:start] + message_content[end:]).strip()

        return tool_content, scraps if scraps else None

    else:
        return message_content.strip() if message_content.strip() else None, None


def _call_ladarag_api(question: str, api_url: str = "http://localhost:5500/api/control/invoke") -> str:
    """
    Directly calls the LADARAG API with a rewritten question.
    This is called by SurveyEngine — NOT by the LLM.

    Args:
        question (str): The rewritten query from queryRewrite tool.
        api_url (str): LADARAG endpoint URL.

    Returns:
        str: Context string from LADARAG, or empty string on failure.
    """
    payload = {"input": question}
    try:
        response = requests.post(api_url, json=payload, timeout=9999)
        response.raise_for_status()
        return str(response.text)
    except Exception as e:
        print(f"[LADARAG] API call failed: {e}")
        return ""


class SurveyEngine:
    def __init__(self, survey_conf: Dict, survey_questions: Dict, agents: List[SurveyAgent], shuffle_response: bool, **kwargs):
        self.survey_conf = survey_conf
        self.questions = survey_questions
        self.agents = agents
        self.respondent_summaries = []
        self.shuffle_response = shuffle_response

    def _enrich_question_with_context(self, agent: SurveyAgent, context: str) -> None:
        """
        Re-asks the queued question enriched with LADARAG context.
        The agent already has the question queued; we augment it here.

        Args:
            agent (SurveyAgent): The survey agent.
            context (str): Context string returned by LADARAG.
        """
        augmented_prompt = (
            f"{agent.queued_question}\n\n"
            f"### External context from LADARAG ###\n"
            f"{context}\n"
            f"### End context ###\n\n"
            f"Using this information if relevant, answer the survey question "
            f"using one of the response tools."
        )
        agent.llm_response(augmented_prompt)

    def run(self):
        # survey logic and tool mapping
        survey_logic = self.survey_conf["logic"]

        for agent in self.agents:
            queued_variable = self.survey_conf["start"]               # queue up first question variable
            queued_question_package = self.questions[queued_variable] # get corresponding question package
            target_dtype = queued_question_package["dtype"]           # tool response dtype should match this

            # logging
            logic_flow =        []
            parsed_responses =  []
            scraps =            []
            encoded_responses = []
            tool_dtypes =       []
            dtype_matches =     []
            n_questions =       1
            bad_iteration =     False

            # question queueing and execution, survey logic control
            while queued_variable is not None:
                queued_question_package = self.questions[queued_variable]
                target_dtype = queued_question_package["dtype"]

                try:
                    # Step 1: Queue and ask the question normally.
                    # The LLM may respond with a survey tool OR with queryRewrite.
                    agent.queue_question(queued_variable, queued_question_package, shuffle_response=self.shuffle_response)
                    agent.ask_question()

                    last_message = agent.message_history[-1]
                    parsed_response, scrap = _response_from_tool_message(last_message)
                except Exception as e:
                    print(f"[SurveyEngine] ask_question failed: {e}")
                    parsed_response = None
                    scrap = None

                # Step 2: If the LLM chose to rewrite the query, handle LADARAG enrichment.
                # This is the SurveyEngine's responsibility — not the LLM's.
                if isinstance(parsed_response, dict) and parsed_response.get("request") == "queryRewrite":

                    rewritten_query = parsed_response.get("question", "")

                    # Step 3: SurveyEngine calls LADARAG directly (no LLM involvement).
                    context = _call_ladarag_api(rewritten_query)

                    # Step 4: Re-ask the original survey question enriched with context.
                    # The LLM must now respond with a proper survey tool.
                    try:
                        self._enrich_question_with_context(agent, context)
                        last_message = agent.message_history[-1]
                        parsed_response, scrap = _response_from_tool_message(last_message)
                    except Exception as e:
                        print(f"[SurveyEngine] enriched question failed: {e}")
                        parsed_response = None
                        scrap = None

                # Step 5: Interpret the final tool response (survey answer).
                match parsed_response:
                    case str():
                        tool_dtype = "TEXT"
                        encoded_response = parsed_response
                    case dict():
                        try:
                            # Exclude queryRewrite from being treated as a valid survey response
                            if parsed_response.get("request") == "queryRewrite":
                                # queryRewrite came back again — treat as bad response
                                tool_dtype = "BADRESPONSE"
                                encoded_response = None
                                bad_iteration = True
                            else:
                                tool_dtype = list(parsed_response.keys())[-1]
                                encoded_response = parsed_response[tool_dtype]
                        except Exception:
                            tool_dtype = "BADRESPONSE"
                            encoded_response = None
                            bad_iteration = True
                    case None:
                        tool_dtype = "BADRESPONSE"
                        encoded_response = None
                        bad_iteration = True
                    case _:
                        print("[SurveyEngine] Unexpected parsed_response type.")
                        tool_dtype = "BADRESPONSE"
                        encoded_response = None
                        bad_iteration = True

                # Step 6: Log responses
                dtype_match = target_dtype == tool_dtype

                logic_flow.append(queued_variable)
                parsed_responses.append(parsed_response)
                scraps.append(scrap)
                encoded_responses.append(encoded_response)
                tool_dtypes.append(tool_dtype)
                dtype_matches.append(dtype_match)
                n_questions += 1

                # Step 7: Advance survey logic
                if survey_logic[queued_variable] is None:
                    break
                elif dtype_match:
                    flag = str(encoded_response)
                    if flag not in survey_logic[queued_variable]:
                        flag = "ELSE"
                elif not dtype_match:
                    flag = "ELSE"
                    bad_iteration = True

                step = survey_logic.get(queued_variable)
                if isinstance(step, dict):
                    queued_variable = step.get(flag) or (step.get("ELSE") if tool_dtype == "NUMERIC" else None)
                elif isinstance(step, str):
                    queued_variable = step
                else:
                    queued_variable = None

            # Package results
            agent_id = agent.config.name
            serial_number = agent.serial_number
            agent_bio = agent.bio

            response_package = AgentResponsePackage(
                agent_id=agent_id,
                agent_bio=agent_bio,
                serial_number=serial_number,
                logic_flow=logic_flow,
                parsed_responses=parsed_responses,
                responses_scraps=scraps,
                encoded_responses=encoded_responses,
                tool_dtypes=tool_dtypes,
                dtype_matches=dtype_matches,
                n_questions=n_questions,
                bad_iteration=bad_iteration)

            self.respondent_summaries.append(response_package)

    def results(self):
        return self.respondent_summaries