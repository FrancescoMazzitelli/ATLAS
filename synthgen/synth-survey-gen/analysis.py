import os
import json
from pathlib import Path
import pandas as pd
import numpy as np
from pandas import DataFrame
from postprocess import ResultsWriter
from verify import  _read_behavioral_analysis, _aggregate_analysis


def load_analysis_config(analysis_folder:str):
    config_path = os.path.join(analysis_folder, "analysis_config.json")

    with open(config_path, "r") as fp:
        analysis_config = json.load(fp)
    return analysis_config

def _get_text_from_logfile(file_path, line_number, sep:str=":"):
    """
    file_path: path to the text file
    line_number: 1-based line number
    """
    with open(file_path, 'r') as f:
        lines = f.readlines()
        if line_number > len(lines) or line_number < 1:
            return None  # Line number out of range
        line = lines[line_number - 1]
        if ':' in line:
            return line.split(sep, 1)[1].strip()  # get text after first ':'
        else:
            return None  # No ':' on this line


def entropy(p):
    p = np.array(p)
    p = p[p > 0]
    return -(p * np.log2(p)).sum()


def normalized_entropy(col):
    values, counts = np.unique(col.dropna(), return_counts=True)
    p = counts / counts.sum()
    H = -(p * np.log2(p)).sum()

    k = len(values)
    if k <= 1:
        return np.float64(0.0)  # define normalized entropy for constant columns

    Hmax = np.log2(k)
    return H / Hmax


def kl_divergence_col(p_col, q_col, epsilon=1e-10):
    """
    Compute KL divergence D_KL(P || Q) for a single column.
    Handles differing unique values.

    P is true Q is predicted
    """
    # All unique values across both columns
    unique_vals = np.union1d(p_col.dropna().unique(), q_col.dropna().unique())

    # Count occurrences for each value
    p_counts = p_col.value_counts().reindex(unique_vals, fill_value=0)
    q_counts = q_col.value_counts().reindex(unique_vals, fill_value=0)

    # Convert to probabilities
    p_probs = (p_counts + epsilon) / (p_counts.sum() + epsilon * len(unique_vals))
    q_probs = (q_counts + epsilon) / (q_counts.sum() + epsilon * len(unique_vals))

    # Compute KL divergence
    return np.sum(p_probs * np.log2(p_probs / q_probs))



def compute_entropy(df: DataFrame, evaluation_variables: list[str]) -> DataFrame:

    entropy_summary = {}

    # compute normalized shannon entropy for each response
    for col in evaluation_variables:

        # filter responses less than 0 (does not apply/I dont know encoding)
        variable_col = pd.to_numeric(df[col], errors="coerce")
        variable_col = variable_col[variable_col >=0]

        entropy_summary[col] = normalized_entropy(variable_col)

    return entropy_summary


def compute_entropy_and_divergence(df: DataFrame, ground_truth_df: DataFrame, evaluation_variables: list[str]) -> tuple[DataFrame]:

    entropy_summary = {}
    divergence_summary = {}

    # compute normalized shannon entropy for each response
    for col in evaluation_variables:

        # filter responses less than 0 (does not apply/I dont know encoding)
        variable_col = pd.to_numeric(df[col], errors="coerce")
        variable_col = variable_col[variable_col >=0]


        # for diveregnce
        ground_truth_col = pd.to_numeric(ground_truth_df[col], errors="coerce")
        ground_truth_col = ground_truth_col[ground_truth_col >=0]

        entropy_summary[col] = normalized_entropy(variable_col)
        divergence_summary[col] = kl_divergence_col(ground_truth_col, variable_col)

    return entropy_summary, divergence_summary


def full_run_evaluation(run_folder: str, config_folder: str, evaluation_variables: list[str], source:str = "US", ground_truth_df = None):

    # get name of model
    log_file_path = os.path.join(run_folder, "log.txt")
    model_name_path = _get_text_from_logfile(log_file_path, 6)
    model_name = model_name_path.split("/")[1]

    # get test df
    rWriter = ResultsWriter(config_folder, run_folder, source=source)
    test_df = rWriter.test_dataset

    # get ground_truth_df
    if source == "US":
        ground_truth_df = pd.read_csv(config_folder.joinpath("data/person.csv"), low_memory=False)

        # do behavioral analysis
        behavioral_analysis_dict = _read_behavioral_analysis(run_folder)
        behavioral_analysis_df = _aggregate_analysis(behavioral_analysis_dict)

        # do entropy and KL divergence
        entropy_summary, divergence_summary = compute_entropy_and_divergence(test_df, ground_truth_df, evaluation_variables)

        # add model name as col to results
        behavioral_analysis_df["model"] = model_name
        entropy_summary["model"] = model_name
        divergence_summary["model"] = model_name

        return model_name, behavioral_analysis_df, entropy_summary, divergence_summary

    if source == "FR":
        behavioral_analysis_dict = _read_behavioral_analysis(run_folder)
        behavioral_analysis_df = _aggregate_analysis(behavioral_analysis_dict)
        entropy_summary = compute_entropy(test_df, evaluation_variables)

        behavioral_analysis_df["model"] = model_name
        entropy_summary["model"] = model_name

        return model_name, test_df, behavioral_analysis_df, entropy_summary


