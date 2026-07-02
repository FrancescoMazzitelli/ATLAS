from preprocess import *
from synthesize import *
from survey import *
from postprocess import ProcessSurveyResponse
from types import SimpleNamespace
from synthesize import SurveyAgent, synthesize_population, _completeEventTool, _timeSelectTool, _ladragNavTool
from pathlib import Path
from types import SimpleNamespace
import pandas as pd



def get_demographic_data(data_path: str|os.PathLike, person: bool = True) -> Tuple[DataFrame, Dict[str, str]]:
    """
    Reads US Census Public Use Microdata Sample (PUMS) Uses 1-year ACS data.
    Househould and person data: https://www2.census.gov/programs-surveys/acs/data/pums/2019/1-Year/
    """
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

    return df, mapper


def get_attr_description(col_names:List[str], row:Series, mapper: Dict[str, str]):
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


def get_agent_config(system_message:str) -> ChatAgentConfig:
    llm_config = lm.OpenAIGPTConfig(
        chat_model=MODEL_PATH,
        chat_context_length=CHAT_CONTEXT_LENGTH,
        temperature=0.5
    )
    agent_config = lr.ChatAgentConfig(
        llm=llm_config,
        system_message=system_message
    )

    return agent_config


def generate_population_descriptions(
    config_folder,
    data_path,
    n_sample: int = 10,
    head: int | None = None,
) -> tuple[list[str], pd.DataFrame|None]:
    """
    Load config, synthesize a population sample, select evaluation variables,
    and return raw agent descriptions.

    Parameters
    ----------
    config_folder : str | Path
        Path to the config directory.
    data_path : str | Path
        Path used by get_demographic_data.
    n_sample : int, default=10
        Number of synthetic population rows to generate.
    head : int | None, default=None
        If provided, only build descriptions for the first `head` rows.

    Returns
    -------
    list[str]
        List of textual descriptions for each sampled agent.
    """
    _, synth_conf, _, _ = load_config(config_folder)
    synth = SimpleNamespace(**synth_conf)

    # Keep if needed for side effects / consistency with existing workflow
    _ = generate_questions(config_folder, source=synth.source)

    population_sample = synthesize_population(
        config_folder=config_folder,
        n_sample=n_sample,
        min_age=18,
        max_age=65
    )
    population_sample = population_sample[population_sample.AGEP.astype(int)>18]

    variables = [
        "AGEP", "ANC1P", "CIT", "CITWP", "COW", "DDRS", "DEAR", "DEYE", "DOUT",
        "DPHY", "DREM", "ESR", "FOD1P", "HICOV", "HINS1", "HINS2", "HINS3",
        "HINS4", "HINS5", "HINS6", "HINS7", "HISP", "LANP", "MAR", "MARHD",
        "MARHYP", "MIL", "NAICSP", "PAOC", "PERNP", "PINCP", "POBP", "POVPIP",
        "POWPUMA", "PRIVCOV", "PUBCOV", "PUMA", "RAC1P", "RAC2P", "RAC3P",
        "RACNUM", "RELSHIPP", "RETP", "SCH", "SCHG", "SCHL", "SCL", "SEX",
        "SSIP", "SSP", "VPS", "WAGP", "WKHP", "WKWN", "YEAR", "YOEP",
    ]

    vars_in_sample = [col for col in population_sample.columns if col in variables]
    evaluation_sample = population_sample[vars_in_sample]

    if head is not None:
        evaluation_sample = evaluation_sample.iloc[:head, :]

    _, mapper = get_demographic_data(data_path)

    descriptions = [
        get_attr_description(list(evaluation_sample.columns), row, mapper)
        for _, row in evaluation_sample.iterrows()
    ]

    return descriptions, population_sample