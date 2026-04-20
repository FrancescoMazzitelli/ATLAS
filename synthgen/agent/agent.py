import json
import pandas as pd
import langroid as lr
import langroid.language_models as lm
import os
from pathlib import Path
from pandas import DataFrame
from langroid.language_models.openai_gpt import OpenAIGPTConfig
from langroid.agent.chat_agent import ChatAgentConfig
from typing import List, Dict, Tuple, Optional
from langroid import ChatDocument

global MODEL_PATH
MODEL_PATH = "ollama/hf-gemma:e4b"

global CHAT_CONTEXT_LENGTH
CHAT_CONTEXT_LENGTH = 16_000

def getDemographicData(data_path: str|os.PathLike, person: bool = True) -> Tuple[DataFrame, Dict[str, str]]:
    """
    Reads US Census Public Use Microdata Sample (PUMS) Uses 1-year ACS data.
    Househould and person data: https://www2.census.gov/programs-surveys/acs/data/pums/2019/1-Year/
    """
    data_path = Path(data_path)
    dd_path = data_path.glob("PUMS_Data_Dictionary*.csv")
    dd = pd.read_csv(*dd_path, header=None, names=list("abcdefg"))
    variable_desc = dd[dd.a == "NAME"].set_index("b")["e"].to_dict()

    na_str = "MISSING"

    if person:
        df_path = data_path.glob("psam_p*.csv")
    else:
        df_path = data_path.glob("psam_h*.csv")
    df = pd.read_csv(*df_path)
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

def getAgentConfig(system_message:str) -> ChatAgentConfig:
    llm_config = lm.OpenAIGPTConfig(
        chat_model=MODEL_PATH,
        chat_context_length=CHAT_CONTEXT_LENGTH
    )
    agent_config = lr.ChatAgentConfig(
        llm=llm_config,
        system_message=system_message
    )

    return agent_config


class PersonaAgent(lr.ChatAgent):
    def __init__(self, config: lr.ChatAgentConfig):
        super().__init__(config)


class AtlasAgent(lr.ChatAgent):
    def __init__(self, config: lr.ChatAgentConfig):
        super().__init__(config)

    def llm_response(self, message: Optional[str | ChatDocument] = None) -> Optional[ChatDocument]:
        return super().llm_response(message)

    def interact(self, message: Optional[str | ChatDocument]):
        self.llm_response(message)