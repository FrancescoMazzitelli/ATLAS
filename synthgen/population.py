from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
import geopandas as gpd
import random
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import json
import re
import locale
import yaml
from geopandas import GeoDataFrame
from shapely import Point
import requests

# locale.setlocale(locale.LC_ALL, "en_US.UTF-8")


def process_MyDailyTravelData(config_folder: str, mydailytravel_source_path: str | None = None):
    """Process MyDailyTravel survey data into question/response dictionary."""
    def value_to_int(x):
        try:
            return int(x)
        except:
            return int(x.split("-")[0])

    def clean_and_replace(text: str, replacements: dict) -> str:
        text = " ".join(text.split())
        def replace_match(match):
            key = match.group(1)
            return replacements.get(key, match.group(0))
        return re.sub(r"\[\$(.*?)\]", replace_match, text)

    replacement_dict = {
        "AGE_COMPUTED": "",
        "ARE_YOU": "are you",
        "ARE_YOU_CAP": "Are you",
        "CURRENTDATE": "today",
        "DAYCARE": "",
        "DO_YOU": "do you",
        "DO_YOU_CAP": "Do you",
        "HAVE_YOU": "have you",
        "I_DO": "I do",
        "JOBTEXT": "",
        "NONWORKER_TEXT": "",
        "ON_DAY": "today",
        "PRIMARY": " primary",
        "WERE_ACTIVITIES": "Were activities",
        "WORK_PRE": "",
        "WORKER_TEXT": "",
        "YOUR": "your",
        "YOUR1": "your",
        "YOUR_EMPLOYER": "your employer",
        "YOUR_THEIR": "your",
        "YOU": "you",
        "YOU1": "you",
        "YOU_DO": "you do",
        "YOU_HAVE": "you",
        "YOU_TELECOMMUTE": "you telecommute",
        "YOU_THEIR": "your",
        "YOU_WORK": "you work"
    }

    config_path = Path(config_folder)
    if mydailytravel_source_path is None:
        mdt_path = config_path / "mydailytravel" / "source"
    else:
        mdt_path = Path(mydailytravel_source_path)

    data_dictionary = pd.read_excel(mdt_path / "data_dictionary.xlsx", sheet_name=None)
    variables_df = data_dictionary["Variables"]
    variables_df = variables_df[variables_df["QUESTION TEXT"].notna()]

    lookup_df = data_dictionary["Value Lookup"]
    lookup_df["VALUE_INT"] = lookup_df["VALUE"].apply(lambda x: value_to_int(x))

    person_cols = pd.read_csv(mdt_path / "person.csv", nrows=0).columns.to_list()

    query_dictionary = {}
    for col in person_cols:
        if col.upper() in variables_df["NAME"].to_list():
            try:
                lookup_table = lookup_df[lookup_df["NAME"]==col.upper()]
                query_dictionary[col.upper()] = {
                    "question":(variables_df[variables_df["NAME"] == col.upper()]["QUESTION TEXT"].values)[0],
                    "dtype":(variables_df[variables_df["NAME"] == col.upper()]["DATA TYPE"].values)[0],
                    "response": lookup_table.set_index("VALUE_INT")["LABEL"].to_dict()
                }
            except:
                query_dictionary[col.upper()] = "This didnt work"

    for item in query_dictionary.items():
        survey_variable, question_response = item
        if "question" in question_response.keys():
            question_text = query_dictionary[survey_variable]["question"]
            query_dictionary[survey_variable]["question"] = clean_and_replace(question_text, replacement_dict)

    introduction = {"INTRO":{
        "question": "Please tell us your name and a little about yourself.",
        "dtype": "TEXT",
        "response": {
            "-8": "I don't know",
            "-7": "I prefer not to answer",}}}
    query_dictionary = {**introduction, **query_dictionary}

    return query_dictionary


def generate_questions(config_yaml: str | Path, source: str = "US"):
    """Generate survey questions from config."""
    config_yaml = Path(config_yaml)
    config_dir = config_yaml.parent

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    if source == "US":
        mydailytravel_path = config.get("data", {}).get("mydailytravel_source_path")
        if mydailytravel_path:
            mydailytravel_path = config_dir / mydailytravel_path
        return process_MyDailyTravelData(config_folder=str(config_dir), mydailytravel_source_path=str(mydailytravel_path) if mydailytravel_path else None)
    elif source == "FR":
        raise NotImplementedError("French source not yet implemented")


def synthesize_population(config_yaml: str | Path, n_sample: int, source: str = "US", min_age: int | None = None, max_age: int | None = None, read_from_dataset: bool | None = True, random_state=0) -> pd.DataFrame | None:
    """Synthesize population sample from PUMS data."""
    config_yaml = Path(config_yaml)
    config_dir = config_yaml.parent

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    data_folder = config_dir / "data"
    na_str = "MISSING"

    if source == "US":
        if read_from_dataset:
            popsim_df = pd.read_csv(config_dir / "synth_data/populationsim/output/synthetic_persons.csv")
            pums_df = pd.read_csv(config_dir / "synth_data/populationsim/data/pums_person_chicago.csv", dtype=str)
            puma_gdf = gpd.read_file(config_dir / "synth_data/tl_2019_17_puma10.shp")
            cmap_gdf = gpd.read_file(config_dir / "synth_data/Facility_Planning_Areas_2016.shp")

            cmap_gdf.to_crs(puma_gdf.crs, inplace=True)
            cmap_boundary = cmap_gdf.geometry.union_all()
            puma_in_cmap_gdf = puma_gdf[puma_gdf.geometry.intersects(cmap_boundary)].reset_index(drop=True)

            puma_in_cmap_gdf["STPUMA"] = puma_in_cmap_gdf["PUMACE10"].apply(lambda x: int("17" + str(x)))

            pop_totals = popsim_df.groupby("STPUMA").size().reset_index(name="count")
            pop_totals.columns = ["STPUMA", "POP_COUNT"]
            puma_with_pop_gdf = puma_in_cmap_gdf.merge(pop_totals, how="left", left_on="STPUMA", right_on="STPUMA")
            puma_with_pop_gdf["SHARE"] = puma_with_pop_gdf.POP_COUNT / puma_with_pop_gdf.POP_COUNT.sum()

            pums_in_popsim_df = pums_df[pums_df.SERIALNO.isin(popsim_df.SERIALNO.astype(str).unique())]

            if min_age is not None:
                pums_in_popsim_df = pums_in_popsim_df[pums_in_popsim_df.AGEP.astype(int) >= min_age]
            if max_age is not None:
                pums_in_popsim_df = pums_in_popsim_df[pums_in_popsim_df.AGEP.astype(int) <= max_age]

            samples = []
            for _, row in puma_with_pop_gdf.iterrows():
                STPUMA = str(row.STPUMA)
                share = row.SHARE
                n = max(int(share*n_sample), 1)
                sample = pums_in_popsim_df[pums_in_popsim_df.STPUMA==STPUMA].sample(n=n, replace=False, random_state=0)
                samples.append(sample)

            pums_sample = pd.concat(samples).reset_index(drop=True)

            def pad_numeric_str(val, total_chars):
                return val.zfill(total_chars) if val.isdigit() else val

            pums_sample["POBP"] = pums_sample["POBP"].apply(lambda x: pad_numeric_str(x, 3))
            pums_sample["PUMA"] = pums_sample["PUMA"].apply(lambda x: pad_numeric_str(x, 5))
            pums_sample["SCHL"] = pums_sample["SCHL"].apply(lambda x: pad_numeric_str(str(int(float(x))), 2))
            pums_sample["CITWP"] = pums_sample["CITWP"].astype(float).astype(int).astype(str)
            pums_sample["MIL"] = pums_sample["MIL"].astype(float).astype(int).astype(str)
            pums_sample["WKHP"] = pums_sample["WKHP"].astype(float).astype(int).astype(str)
            pums_sample["WKWN"] = pums_sample["WKWN"].astype(float).astype(int).astype(str)
            pums_sample["COW"] = pums_sample["COW"].astype(float).astype(int).astype(str)

            return pums_sample

    if source == "FR":
        raise NotImplementedError("French source not yet implemented")


def get_demographic_data(data_path: str | Path, person: bool = True) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Load demographic data and mapper from PUMS files."""
    data_path = Path(data_path)
    dd_path = list(data_path.glob("PUMS_Data_Dictionary*.csv"))
    if not dd_path:
        raise FileNotFoundError(f"No PUMS_Data_Dictionary*.csv found in {data_path}. Please check your pums_data_path in config.yaml")
    dd = pd.read_csv(dd_path[0], header=None, names=list("abcdefg"))
    variable_desc = dd[dd.a == "NAME"].set_index("b")["e"].to_dict()

    na_str = "MISSING"

    if person:
        df_path = list(data_path.glob("psam_p*.csv"))
    else:
        df_path = list(data_path.glob("psam_h*.csv"))
    if not df_path:
        raise FileNotFoundError(f"No psam_p*.csv or psam_h*.csv found in {data_path}. Please check your pums_data_path in config.yaml")
    df = pd.read_csv(df_path[0], dtype="str")
    pums_variables = df.columns.values
    pums_variable_dict = {k:v for k,v in variable_desc.items() if k in pums_variables}
    mapper = {}
    for variable, description in pums_variable_dict.items():
        description_row = dd[(dd.a!="NAME") & (dd.b==variable)]
        dtype = description_row.c.iloc[0]
        answers = description_row[["f","g"]] \
            .drop_duplicates() \
            .fillna(na_str) \
            .set_index("f")["g"] \
            .to_dict()
        mapper[variable] = {
            "description": description,
            "dtype": dtype,
            "answers": answers
        }

    return df, mapper


def get_attr_description(col_names: List[str], row: pd.Series, mapper: Dict[str, str]) -> str:
    """Generate textual description of person attributes."""
    attrs = []
    for var, val in zip(col_names, row):
        var_package = mapper[var]
        encoded_var = var_package["answers"].get(str(val))
        assert isinstance(var_package, dict)
        if var == "AGEP":
            attrs.append(f"Age: {val}")
        elif encoded_var and encoded_var not in ["No", "MISSING"]:
            desc = var_package.get("description")
            attrs.append(f"{desc}: {encoded_var}")
    return "\n".join(attrs)
