import sys
import os
import json
import re
from tqdm import tqdm
from pathlib import Path
import pandas as pd
from pandas import DataFrame
from preprocess import generate_questions
from ollama import chat

global evaluator_prompt
evaluator_prompt = """
You are an expert evaluator of responses to survey questions.
Your task is to analyze the provided responses according to the following three metrics:
1. Self-Consistency (Internal Consistency/Logical Coherence)
2. Relevance & Specificity (Topical Relevance/Response Specificity)
3. Empathy & Tone Appropriateness (Affective Appropriateness/Empathic Accuracy)

However, some responses may contain artifacting from API or tool usage, such as improper syntax, malformed JSON snippets, or references to the artifacting itself. Some responses may consist soley of artifacting or are mainly incomplete.
- If the response is malformed or nonsensical, set "malformed_response": true and provide a brief reason.
- If the response consists mainly of artifacting or mainly incomplete, set "malformed_response": true and provide a brief reason.
- If the response is valid and can be evaluated, set "malformed_response": false.

For each metric, provide a score (1-5), a brief justification, and verify if the response is malformed. Your scores should also reflect the number of responses provided, typically between 10 to 30.

Use the following JSON format for your response:
```json
{
  "self_consistency": {
    "score": <int>,
    "justification": "<str>"
  },
  "relevance_and_specificity": {
    "score": <int>,
    "justification": "<str>"
  },
  "empathy_and_tone": {
    "score": <int>,
    "justification": "<str>"
  },
  "malformed_response": <bool>,
  "malformed_reason": "<str>"  // Only include if malformed_response is true
}
```

Scoring Guide:
1: Poor
2: Below Average
3: Average
4: Good
5: Excellent

Example (Malformed Response):
```json
{
  "malformed_response": true,
  "malformed_reason": "Response contains only JSON artifacting and no evaluable content."
}
```

Example (Valid Response):
```json
{
  "self_consistency": {
    "score": 4,
    "justification": "The response is logically consistent with previous answers."
  },
  "relevance_and_specificity": {
    "score": 5,
    "justification": "The response directly addresses the question and provides specific details."
  },
  "empathy_and_tone": {
    "score": 3,
    "justification": "The tone is appropriate but could be more empathetic."
  },
  "malformed_response": false
}
```

Now, evaluate the following survey responses:

"""

def read_results_json(run_path: str):
    folder_path = Path(run_path)
    folder_name = folder_path.name

    run_date = folder_name.split("_", 1)[1]
    results_json = list(folder_path.glob(run_date+"_results.json"))[0]
    with open(results_json, "r") as f:
        results_json = json.load(f)
    return results_json


def _remove_substrings(text, remove_list):
    for s in remove_list:
        text = text.replace(s, "")
    text = text.strip().replace("  ", " ")
    return text.strip()


def prepare_questions_and_responses(response: dict, questions:dict, remove_list: list, style: str = "question_response") -> str | None:
    raw_response_scraps = response.get("responses_scraps")
    survey_question_variables = response.get("logic_flow")

    responses_scraps = [s for s in raw_response_scraps if isinstance(s, str)]
    indices_to_remove = [i for i, s in enumerate(raw_response_scraps) if not isinstance(s, str)]

    filtered_survey_variables = [item for i, item in enumerate(survey_question_variables) if i not in indices_to_remove]
    cleaned_responses_scraps = [_remove_substrings(s, remove_list) for s in responses_scraps]

    formatted_responses = []
    i = 1
    for response in cleaned_responses_scraps:
        if (response != "") and (response != None):
            formatted_responses.append(
                f"{i}. {response}"
            )
            i += 1

    final_dialog = ""

    if len(formatted_responses) == len(filtered_survey_variables):
        for i, question_response in enumerate(zip(filtered_survey_variables, cleaned_responses_scraps)):
            survey_variable, survey_response = question_response
            survey_question = questions.get(survey_variable).get("question")

            if style == "question_response":
                dialog = f"Question {i+1}: {survey_question}\nResponse: {survey_response}"
            elif style == "response_only":
                dialog = f"Response {i+1}: {survey_response}"

            final_dialog += dialog + "\n"

        return final_dialog

    else:
        return None

def _extract_json_evaluator_response(text: str):
    """
    Extract JSON contained inside triple backticks ```json ... ```
    and return it as a Python dict.
    """
    # Look for a fenced code block labelled json
    pattern = r"```json(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)

    if not match:
        return None
        # raise ValueError("No JSON code block found.")

    json_str = match.group(1).strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        return None
        # raise ValueError(f"Invalid JSON detected: {e}")

def evaluator_get_ratings(evaluator_prompt: str, agent_response:str, model: str):
    response = chat(
        model="llama3.1:8b",
        messages=[
            {"role": "system", "content": evaluator_prompt},
            {"role": "user", "content": agent_response}
        ]).message.content

    return(_extract_json_evaluator_response(response))


def _read_behavioral_analysis(run_path: str) -> list[dict]:
    file_path = os.path.join(run_path, "behavioral_analysis.json")
    with open(file_path, "r") as f:
        behavioral_analysis: dict = json.load(f)
        return behavioral_analysis


def _check_malformed(behavioral_analysis: list[dict]) -> tuple[int]:
    n_analyses = len(behavioral_analysis)
    n_malformed = 0
    for analysis in behavioral_analysis:
        if analysis.get("malformed_response") == True: n_malformed += 1

    return n_analyses, n_malformed

def _get_score_deep(analysis: dict, behavior_key: str, metric_key: str = "score"):
    metric = analysis.get(behavior_key)
    if metric:
        score = metric[metric_key]
        return score
    else:
        return None


def _aggregate_analysis(behavioral_analysis: list[dict]):

    behavior_scores = []
    for analysis in behavioral_analysis:
        score = {
            "agent_id": analysis.get("ID"),
            "malformed": analysis.get("malformed_response"),
            "malformed_reason": analysis.get("malformed_reason"),
            "consistency": _get_score_deep(analysis, "self_consistency"),
            "relevance": _get_score_deep(analysis, "relevance_and_specificity"),
            "tone": _get_score_deep(analysis, "empathy_and_tone"),
            "consistency_justification": _get_score_deep(analysis, "self_consistency", "justification"),
            "relevance_justification": _get_score_deep(analysis, "relevance_and_specificity", "justification"),
            "tone_justification": _get_score_deep(analysis, "empathy_and_tone", "justification")
        }
        behavior_scores.append(score)

    scores_df = DataFrame(behavior_scores)
    return scores_df

def main():
    if len(sys.argv) < 3:
        print("Usage: python main.py path/to/config/ path/to/result_folder [model_name]")
        sys.exit(1)

    CONFIG_FOLDER = sys.argv[1]
    RESULT_FOLDER = sys.argv[2]
    if len(sys.argv) > 3:
        MODEL_NAME = sys.argv[3]
    else:
        MODEL_NAME = "llama3.1:8b"
        print(f"Default model selected ({MODEL_NAME})")

    # get results and survey questions
    run_results = read_results_json(RESULT_FOLDER)
    questions = generate_questions(CONFIG_FOLDER)

    remove_list = [
        "TOOL: ",
        "TEXT:",
        "request",
        "discreteNumericResponse",
        "singleAnswerResponse",
        "multipleAnswerResponse",
        "tool",
        "TEXT",
        "{",
        "}",
        "[",
        "]",
        ":",
        '"',
        "\n",
    ]

    print(CONFIG_FOLDER)
    print(RESULT_FOLDER)
    print(MODEL_NAME)

    # evaluation logging
    n_right = 0
    n_wrong_evaluator = 0
    n_wrong_response = 0

    # rate responses of each agent in the run
    ratings = []
    for result in tqdm(run_results):

        # log agent ID
        agent_id = result.get("agent_id")

        # prepare agent questions and answers
        agent_response = prepare_questions_and_responses(result, questions, remove_list, style="response_only")

        if agent_response:

            rating = evaluator_get_ratings(evaluator_prompt, agent_response, MODEL_NAME)

            if rating:
                rating["ID"] = agent_id
                ratings.append(rating)
                n_right += 1
            else:
                n_wrong_evaluator += 1
        else:
            n_wrong_response += 1

        tqdm.write(f"\nGood eval:\t{n_right}\nBad input:\t{n_wrong_response}\nBad eval:\t{n_wrong_evaluator}")

    ratings_file = os.path.join(RESULT_FOLDER, "behavioral_analysis.json")

    with open(ratings_file, "w") as f:
        json.dump(ratings, f, indent=4)



if __name__ == "__main__":
    main()
