from typing import Dict, List, Any, Optional
import pandas as pd
import geopandas as gpd
import random
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import json
import re
import locale

"""
Preprocessing steps for census and travel survey data.
These functions prepare json files for a specific survey
configuration, however there are a couple steps (~10%)
that needs to be done manually.
"""

# set for US currency atm
locale.setlocale(locale.LC_ALL, "en_US.UTF-8") # this will be a problem in the future


def process_MyDailyTravelData(config_folder: str):
    def value_to_int(x):
        try:
            return int(x)
        except:
            return int(x.split("-")[0])

    def clean_and_replace(text: str, replacements: dict) -> str:

        text = " ".join(text.split())  # Remove excess whitespace and new lines

        def replace_match(match):
            key = match.group(1)
            return replacements.get(key, match.group(0))  # Replace if found, else keep original

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

    # get variables and table from data dictionary
    data_path = Path(config_folder) / "data"
    file_path = data_path / "data_dictionary.xlsx"
    data_dictionary = pd.read_excel(file_path, sheet_name=None)
    variables_df = data_dictionary["Variables"]
    variables_df = variables_df[variables_df["QUESTION TEXT"].notna()]

    lookup_df = data_dictionary["Value Lookup"]
    lookup_df["VALUE_INT"] = lookup_df["VALUE"].apply(
        lambda x: value_to_int(x)
        )

    # get response dictionary from person.csv
    file_path = data_path / "person.csv"
    person_cols = pd.read_csv(file_path, nrows=0).columns.to_list()

    # query dictionary
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

    # add introduction
    introduction = {"INTRO":{
        "question": "Please tell us your name and a little about yourself.",
        "dtype": "TEXT",
        "response": {
            "-8": "I don't know",
            "-7": "I prefer not to answer",}}}
    query_dictionary = {**introduction, **query_dictionary}

    return query_dictionary


def process_pums_data(config_folder: str, person: bool = True, write: str | None = None) -> Dict[str, str] | None:
    """
    Reads US Census Public Use Microdata Sample (PUMS) Uses 1-year ACS data.
    Househould and person data: https://www2.census.gov/programs-surveys/acs/data/pums/2019/1-Year/
    """
    data_path = Path(config_folder) / "data"
    dd_path = data_path.glob("PUMS_Data_Dictionary*.csv")
    dd = pd.read_csv(*dd_path, header=None, names=list("abcdefg"))
    variable_desc = dd[dd.a == "NAME"].set_index("b")["e"].to_dict()

    na_str = "MISSING"

    if person:
        df_path = data_path.glob("psam_p*.csv")
    else:
        df_path = data_path.glob("psam_h*.csv")
    df = pd.read_csv(*df_path, nrows=1)
    pums_variables = df.columns.values
    pums_variable_dict = {k:v for k,v in variable_desc.items() if k in pums_variables}
    mapper = {}
    for variable,description in pums_variable_dict.items():
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

    if write != None:
        with open(write, "w") as file:
            json.dump(mapper, file, indent=4)
    else:
        return mapper


def puma_locations(config_folder: str) -> Dict[str, List[str]]:
    """
    returns a dictionary of PUMA codes keys and list of cities within PUMA.
    derived from:https://catalog.data.gov/dataset/tiger-line-shapefile-2019-state-illinois-current-place-state-based
    """
    epsg = 26971 # NAD83 StatePlane Illinois East FIPS 1201
    data_path = Path(config_folder) / "data"
    puma = gpd.read_file(data_path / "tl_2019_17_puma10.shp").to_crs(epsg=epsg)
    place = gpd.read_file(data_path / "tl_2019_17_place.shp").to_crs(epsg=epsg)

    place["geometry"] = place.geometry.centroid
    joined_place = gpd.sjoin_nearest(place, puma, how="left")

    puma_locations = joined_place[["NAME", "PUMACE10"]].groupby("PUMACE10")["NAME"].apply(list).to_dict()
    return puma_locations


def attribute_decoder_dict(encoded_attributes: Dict[str, str], decoder_dict: Dict[Any, Any]) -> Dict[str, str]:
    """
    prepares a dictionary of encoded individual attributes and their
    descriptions (from the person.json and house.json files) for system prompt templating
    """
    individual_attributes = {}
    keyCatch = ["SERIALNO", "PUMA"]
    for key, val in encoded_attributes.items():
        try:
                if (decoder_dict[key]["dtype"] == "N") or (key in keyCatch) :
                    individual_attributes[key] = val
                else:
                    individual_attributes[key] = decoder_dict[key]["answers"][val]
        except:
            individual_attributes[key] = "MISSING"

    return individual_attributes


def get_attribute_descriptions(decoder_dict: Dict[Any, Any]) -> Dict[str, str]:
    return {key+"_desc": decoder_dict[key]["description"] for key in decoder_dict.keys()}


def _get_fiche_responses(excel_path: str):
    # read df, get var name series and var response df
    df = pd.read_excel(excel_path, sheet_name=1)

    # get list of list of variables that corresponds to grouped response options
    var_series = df.iloc[:,0]
    var_series = var_series[~var_series.str.contains("FILTRE", na=False)]

    # group on 'islands' of vars
    mask =var_series.notna()
    groups = (mask != mask.shift()).cumsum()
    var_groups = [group.to_list() for key, group in var_series.groupby(groups) if group.notna().any()]

    # concat and convert groups of vars to one list
    formatted_var_groups = []
    for group in var_groups:
        if len(group) == 1 and not "," in group[0]:
            formatted_var_groups.append(group)
        elif len(group) == 1:
            formatted_group = [var.strip() for var in group[0].split(",")]
            formatted_var_groups.append(formatted_group)
        elif len(group) >= 1:
            formatted_vars = []
            for subgroup in group:
                formatted_subgroup = [var.strip() for var in subgroup.split(",")]
                formatted_vars.extend(formatted_subgroup)
            formatted_var_groups.append(formatted_vars)
        else:
            print(f"Group {group} not formatted")

    # group response options - if correct should be the same
    # length as formatted_var_groups
    response_options_df = df.iloc[:,1:]

    # get mask and index for each group
    mask = response_options_df.iloc[:,1].notna()
    group_id = (mask != mask.shift()).cumsum()
    filtered_groups = response_options_df[mask].groupby(group_id)
    chunks = [group for _, group in filtered_groups]

    return formatted_var_groups, chunks


def _df_to_dict(df: pd.DataFrame):
    df = df.dropna()
    try:
        converted_dict = dict(zip(df["FICHE MENAGE"].astype(int).astype(str), df["Unnamed: 2"]))
    except:
        converted_dict = dict(zip(df["FICHE MENAGE"].astype(str), df["Unnamed: 2"]))
    return converted_dict


def process_EnqueteMenagesDeplacements(config_folder:str) -> dict:
    data_path = Path(config_folder) / "data"
    data_dictionary_path = data_path / "Dessin_fichier_Dictionnaire_variables_EDGT_AML_Face-a-Face_02082015.xls"
    questions_path = Path(config_folder) / "questions.csv"

    # questions df
    questions_df = pd.read_csv(questions_path, header=None, names=["var", "question"])

    # numeric vars
    numeric_vars = [
        "M6", "M9", "M10", "M14",
        "M18", "M21", "M22", "P4",
        "D1", "D3", "D4", "D7", "D8",
        "D9", "D10", "T1", "T2", "T4",
        "T5", "T6", "T7", "T8", "T8a",
        "T8b", "T11"]


    # variables and responses from data dictionary
    var_groups, chunks = _get_fiche_responses(data_dictionary_path)

    formatted_responses = [_df_to_dict(chunk) for chunk in chunks]
    question_vars = questions_df.iloc[:,0].to_list()

    query_dictionary = {}
    re_ignore = ["JOURDEP", "M12A"]

    for group_index, var_group in enumerate(var_groups):
        for var in var_group:
            if var not in re_ignore:
                var = re.sub(r'([A-Z]+\d+)(?:[A-Z]$|-\d+$)', r'\1', var)
            if var in question_vars:
                query_dictionary[var] = {
                    "question": questions_df[questions_df["var"] == var]["question"].values[0],
                    "dtype": "TEXT" if var not in numeric_vars else "NUMERIC",
                    "response": formatted_responses[group_index]
                }

    # add intro
    query_dictionary["INTRO"] = {
        "question": "Parlez-nous un peu de vous.",
        "dtype": "TEXT",
        "response": {
            "-8": "I don't know",
            "-7": "I prefer not to answer",}}

    return query_dictionary


def process_insee_census(config_folder):
    """
    Reads INSEE census data description.
    From: https://www.insee.fr/fr/statistiques/8268848
    """
    data_path = Path(config_folder) / "data"
    df_path = data_path / "varmod_indcvi_2021.csv"
    df = pd.read_csv(df_path, sep=";")

    insee_variables = df.COD_VAR.unique()

    mapper = {}
    for var in insee_variables:

        # get variable description
        filtered_df = df[df.COD_VAR==var]
        row_match = filtered_df.iloc[0]
        description = row_match.LIB_VAR
        dtype = row_match.TYPE_VAR

        answers = dict(zip(
            filtered_df["COD_MOD"],
            filtered_df["LIB_MOD"]
        ))

        mapper[var] = {
            "description": description,
            "dtype": dtype,
            "answers": answers
        }

    return mapper


def generate_questions(config_folder: str, source: str = "US"):
    if source == "US":
        return process_MyDailyTravelData(config_folder=config_folder)
    elif source == "FR":
        return process_EnqueteMenagesDeplacements(config_folder=config_folder)


def _decapitalize(sentence: str)->str:
    """
    Returns sentence without capitalization
    """
    return sentence[0].lower()+sentence[1:]


def _indefinite(noun_phrase: str) -> str:
    """
    Adds an indefinite article to noun phrase
    """
    vowels = "aeiou"
    indefinite_article = "an" if noun_phrase[0].lower() in vowels else "a"
    return f"{indefinite_article} {noun_phrase}"


def _wspace(word: str) -> str:
    "Adds singe space to start of phrase"
    return f" {word}"


def _random_select(key: str, mapper: Dict[str, List[str]]):
    """Takes a string phrase as a key and returns a random selection from mapper dictionary

    Args:
        key (str): jinja template variable
        mapper (Dict[str, List[str]]): A dictionary with items corresponding to jinja environment variables and there possible values

    Returns:
        _type_: _description_
    """
    values: Optional[List[str]] = mapper.get(key)
    if values:
        return random.choice(values)
    return "RANDOM_SELECT_MISSING"


def _decontext(phrase: str, seps: list[str] = [" (", " ;"]) -> str:
    """
    Removes anything in phrase after the first occurrence of any separator in `seps`.
    """
    first_split_index = len(phrase)
    for sep in seps:
        if sep in phrase:
            idx = phrase.index(sep)
            if idx < first_split_index:
                first_split_index = idx
    return phrase[:first_split_index]


def _to_currency(val: str | float, symbol: bool = True, grouping: bool = True):
    return locale.currency(float(val), symbol=True, grouping=grouping)[:-3]


def _random_includes(seq: List[str]) -> List[str]:
    shuffled = list(seq)
    random.shuffle(shuffled)
    return shuffled


def _listify(items: List[str]) -> str:
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    elif len(items) == 2:
        return f'{items[0]} and {items[1]}'
    else:
        return f"{', '.join(items[:-1])}, and {items[-1]}"


def _fr_ANEMR_select(ANEMR_response: str) -> str:
    """
    Returns a formatted string resposne
    to the INSEE survey question ANEMR:

    'Ancienneté d'emménagement dans le logement (regroupée)'

    Args:
        ANEMR_response (str): Response from ANEMR variable

    Returns:
        str: formatted string for templating
    """

    years = [int(num) for num in re.findall(r"-?\d+", ANEMR_response)]

    if not years:
        return "" # Hors logement odrinaire - atypical situation
    elif years and years[0] == 2:
        return "depuis moins de 2 ans"
    elif years[0] == 70:
        return "depuis plus de 70 ans"
    else:
        min, max = years
        selected_year = random.randint(min, max)
        return f"depuis {selected_year} ans"


def _fr_mariage(SEXE: str) -> str:
    """
    Returns appropriate partner of married couple
    """

    if SEXE == "Hommes":
        return "épouse"
    else:
        return "mari"

def _fr_couple(SEXE: str) -> str:
    if SEXE == "Hommes":
        return "petit amie"
    else:
        return "petit ami"


class SystemMessageGenerator:
    def __init__(self, config_folder: str, template: str, verbose_debug:bool = False, shuffle:bool = False, wrap: int|None = None):
        """
        WRAP IS DEFAULT BEHAVIOR ON SYS MSG
        """
        # load environment
        self.env = Environment(
            loader=FileSystemLoader(
                Path(config_folder) / "templates"
            ),
            extensions=["jinja2.ext.do"]
        )
        self.template = template
        self.verbose_debug = verbose_debug
        self.shuffle = shuffle
        self.wrap = wrap

        # initialize environment preferences and filters
        self.env.trim_blocks=True
        self.env.lstrip_blocks=True
        self.env.filters["desentence"]         = _decapitalize
        self.env.filters["indefinite"]         = _indefinite
        self.env.filters["wspace"]             = _wspace
        self.env.filters["random_s"]           = _random_select
        self.env.filters["decontext"]          = _decontext
        self.env.filters["to_currency"]        = _to_currency
        self.env.filters["randomize_includes"] = _random_includes
        self.env.filters["listify"]            = _listify
        self.env.filters["ANEMR_select"]       = _fr_ANEMR_select
        self.env.filters["SEXE_mariage"]       = _fr_mariage
        self.env.filters["SEXE_couple"]        = _fr_couple

        # get system message template
        self.system_message_template = self.env.get_template(self.template)

    def _wrap_text(self, text, n):
        """
        Utility to break text on char width
        """
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            if sum(len(w) for w in current_line) + len(current_line) + len(word) <= n:
                current_line.append(word)
            else:
                # Join current line and start a new one
                lines.append(' '.join(current_line))
                current_line = [word]

        # Add last line if not empty
        if current_line:
            lines.append(' '.join(current_line))

        return '\n'.join(lines)

    def write_system_message(self, **kwargs) -> str:
        """Writes agent system messages

        kwargs are specific to population synthesis dataset

        Returns:
            str: _description_
        """
        rendered_msg = self.system_message_template.render(**kwargs, _all_args = kwargs, verbose_debug = self.verbose_debug, shuffle = self.shuffle)
        if self.wrap is not None:
            return self._wrap_text(rendered_msg, self.wrap)
        else:
            return rendered_msg[1:] # there is an extra space on intro to make shuffling work


if __name__ == "__main__":
    pass