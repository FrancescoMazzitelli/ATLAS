import sys
import os
from pathlib import Path
import threading
import time
from queue import Queue, Empty
from tqdm import tqdm
from typing import Dict, List
from preprocess import generate_questions
from synthesize import load_config, build_agents, SurveyAgent
from survey import SurveyEngine
from postprocess import ProcessSurveyResponse
from types import SimpleNamespace
from langroid.utils.configuration import settings

if len(sys.argv) < 2:
    print("Usage: python main.py path/to/config/ [path/to/runfolder]")
    sys.exit(1)

config_folder = sys.argv[1]
RUN_FOLDER = sys.argv[2] if len(sys.argv) > 2 else None

settings.quiet = True

def run_survey(
    result_queue: Queue,
    stop_event: threading.Event,
    survey_conf: Dict,
    questions: Dict,
    agents: List[SurveyAgent],
    batch_size: int,
    shuffle_response: bool,
    timeout_per_batch: float = 600000  # seconds
):
    try:
        for i in tqdm(range(0, len(agents), batch_size), desc="running batches"):
            batch = agents[i: i+batch_size]

            def batch_runner():
                nonlocal batch_results
                SE = SurveyEngine(survey_conf, questions, batch, shuffle_response)
                SE.run()
                batch_results = SE.results()

            batch_results = []
            thread = threading.Thread(target=batch_runner)
            thread.start()
            thread.join(timeout=timeout_per_batch)

            if thread.is_alive():
                print(f"Batch {i // batch_size} timed out. Skipping.")
                continue  # skip to next batch

            for r in batch_results:
                result_queue.put(r)

    except Exception as e:
        print(f"Exception in run_survey: {e}")
    finally:
        stop_event.set()


def postprocess_response(
    result_queue: Queue,
    stop_event: threading.Event,
    postprocessor: ProcessSurveyResponse,
    date_str: str):
    while not stop_event.is_set() or not result_queue.empty():
        try:
            result = result_queue.get(timeout=1.0)
            postprocessor.serialize_response(result)
        except Empty:
            continue
        except Exception as e:
            print(f"Error at postprocess thread: {e}")
            print(type(e))


def main():
    global RUN_FOLDER

    start_time = time.time()
    date_str = time.strftime("%Y%m%d_%H%M")
    name_str = Path(config_folder).name
    dir_name = "_".join((name_str, date_str))

    if RUN_FOLDER is None:
        dir_name = "_".join((name_str, date_str))
        RUN_FOLDER = os.path.join("run", dir_name)
    else:
        RUN_FOLDER = os.path.join(RUN_FOLDER, dir_name)

    os.makedirs(RUN_FOLDER, exist_ok=True)

    model_conf, synth_conf, survey_conf, analysis = load_config(config_folder)

    synth = SimpleNamespace(**synth_conf)
    n = synth.sample_size
    subsample = synth.subsample
    batch_size = synth.batch_size
    source = synth.source
    shuffle_response = synth.shuffle_response
    shuffle_prompt = synth.shuffle_response
    wrap = synth.wrap

    questions = generate_questions(config_folder, source=source) # needs source config

    # add shuffle here please
    agents, population_sample = build_agents(
        config_folder,
        n=n,
        subsample=subsample,
        source=source,
        shuffle=shuffle_prompt,
        wrap=wrap)

    # needs source config
    postprocessor = ProcessSurveyResponse(
        config_folder,
        batch_size=batch_size,
        RUN_FOLDER=RUN_FOLDER,
        source=source,
        date_str=date_str)

    result_queue = Queue()

    stop_event = threading.Event()

    survey_thread = threading.Thread(
        target=run_survey,
        args=(
            result_queue,
            stop_event,
            survey_conf,
            questions,
            agents,
            batch_size,
            shuffle_response))

    postprocessing_thread = threading.Thread(
        target=postprocess_response,
        args=(result_queue, stop_event, postprocessor, date_str))

    survey_thread.start()
    postprocessing_thread.start()
    survey_thread.join()
    postprocessing_thread.join()

    end_time = time.time()
    duration_hour = (end_time - start_time) / 3600.00

    write_success = postprocessor.write_results(RUN_FOLDER, date_str)

    # logging
    population_sample.to_csv(os.path.join(RUN_FOLDER, "_".join((date_str, "population_sample.csv"))), index=False)

    log_path = os.path.join(RUN_FOLDER, "log.txt")
    with open(log_path, "w") as f:
        f.write(f"Start Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}\n")
        f.write(f"End Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}\n")
        f.write(f"Duration (hours): {duration_hour:.2f}\n")
        f.write(f"Successful write to disk: {write_success}\n")

        f.write("Model Configuration:\n")
        for key, value in model_conf.items():
            f.write(f"{key}: {value}\n")

        f.write("\nSynthesis Configuration:\n")
        for key, value in synth_conf.items():
            f.write(f"{key}: {value}\n")

if __name__ == "__main__":
    main()