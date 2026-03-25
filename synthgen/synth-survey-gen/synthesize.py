import pandas as pd
import geopandas as gpd
from geopandas import GeoDataFrame
from shapely import Point
import json
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import langroid as lr
import langroid.language_models as lm
from langroid.agent.chat_agent import ChatDocument
from preprocess import *
import requests


def load_config(config_folder:str):
    """
    Prepares config folder
    """
    config_path = Path(config_folder)

    # read config, question, and mappers
    with open(config_path / "config.json", "r") as file:
        config: dict = json.load(file)

    return config.values()


def synthesize_population(config_folder:str, n_sample:int, source:str="US", min_age: int|None = None, max_age: int|None = None, read_from_dataset: bool|None = True, random_state=0) -> pd.DataFrame | None:
    """
    Returns a spatially proportional sample of the PUMS dataset based on CMAP My Daily Travel Survey respondent sample.
    """
    data_folder = Path(config_folder) / "data"
    na_str = "MISSING"

    if source == "US":
        if not read_from_dataset:
            """
            Data derived from 2019 Public Use Microdata Sample (PUMS) dataset.
            PUMS: https://www.census.gov/programs-surveys/acs/microdata/access.2019.html#list-tab-735824205
            PUMA: https://catalog.data.gov/dataset/tiger-line-shapefile-2019-series-information-for-the-2010-census-public-use-microdata-area-puma
            """

            crs:str = "EPSG:4326"

            # load PUMS and PUMA data
            person_df = pd.read_csv(data_folder/"person.csv", low_memory=False)
            location_df = pd.read_csv(data_folder/"location.csv")

            pums_person_df = pd.read_csv(data_folder/"psam_p17.csv", dtype=str)
            if min_age is not None:
                pums_person_df = pums_person_df[pums_person_df.AGEP.astype(int) >= min_age]
            if max_age is not None:
                pums_person_df = pums_person_df[pums_person_df.AGEP.astype(int) <= max_age]

            puma_gdf = gpd.read_file(data_folder/"tl_2019_17_puma10.shp")
            puma_gdf = puma_gdf.to_crs(crs=crs)
            puma_gdf["PUMA"] = puma_gdf["PUMACE10"].astype(int)

            # get the size of each household
            household_sizes = person_df[["sampno", "perno"]].groupby(by="sampno")["perno"].max()
            household_locations = location_df[location_df.loctype==1] \
                .drop_duplicates(subset="sampno") \
                .set_index("sampno")[["longitude", "latitude"]] # always x, y for spatial operations apparently
            household_locations = household_locations.join(household_sizes)

            # get household locations as gdf
            geometry = household_locations.apply(
                lambda row: Point(row.longitude, row.latitude), axis=1
            )
            household_gdf = GeoDataFrame(household_locations, geometry=geometry, crs=crs)

            # get count of households in each PUMA zone
            household_join = household_gdf.sjoin(puma_gdf, predicate="within")
            household_counts = household_join.groupby(by=["PUMA"])["perno"].sum()
            puma_household_counts = puma_gdf \
                .set_index("PUMA") \
                .join(household_counts, how="inner")[["GEOID10", "PUMACE10", "NAMELSAD10", "perno", "geometry"]]
            puma_household_counts.reset_index(inplace=True)

            # sample by PUMA area
            samples = []
            puma_household_counts["weight"] = puma_household_counts.perno / puma_household_counts.perno.sum()
            for _, row in puma_household_counts.iterrows():
                puma = row.PUMACE10
                weight = row.weight
                n = max(int(weight*n_sample), 1)

                sample = pums_person_df[pums_person_df.PUMA==puma].sample(n, random_state=random_state)
                samples.append(sample)

            population_sample = pd.concat(samples)
            population_sample.fillna(na_str, inplace=True)

            return population_sample

        if read_from_dataset:
            """
            Read from Chicago synthetic population created with https://activitysim.github.io/populationsim/
            using inputs from https://polaris.taps.anl.gov/polaris-studio/prepare/population_synthesis.html
            """

            config_folder = Path(config_folder)

            # get populationsim synthetic population
            popsim_df = pd.read_csv(config_folder / "data/populationsim/output/synthetic_persons.csv")

            # get PUMS dataset from POLARIS
            pums_df = pd.read_csv(config_folder / "data/populationsim/data/pums_person_chicago.csv", dtype=str)

            # get PUMS PUMA geography
            # https://catalog.data.gov/dataset/tiger-line-shapefile-2019-2010-state-illinois-2010-census-public-use-microdata-area-puma-state-
            puma_gdf = gpd.read_file(config_folder / "data/tl_2019_17_puma10.shp")

            # get CMAP planning area
            # https://datahub.cmap.illinois.gov/datasets/4834d52310d24e56a0300898a0cb23bc_0/explore
            cmap_gdf = gpd.read_file(config_folder / "data/Facility_Planning_Areas_2016.shp")

            # get puma areas within cmap planning boundary
            cmap_gdf.to_crs(puma_gdf.crs, inplace=True)
            cmap_boundary = cmap_gdf.geometry.union_all()
            puma_in_cmap_gdf = puma_gdf[puma_gdf.geometry.intersects(cmap_boundary)].reset_index(drop=True)

            # add STPUMA to puma_in_cmap gdf
            puma_in_cmap_gdf["STPUMA"] = puma_in_cmap_gdf["PUMACE10"].apply(
                lambda x: int("17" + str(x))
            )

            # get population totals by STPUMA from popsim df
            pop_totals = popsim_df.groupby("STPUMA").size().reset_index(name="count")
            pop_totals.columns = ["STPUMA", "POP_COUNT"]
            puma_with_pop_gdf = puma_in_cmap_gdf.merge(pop_totals, how="left", left_on="STPUMA", right_on="STPUMA")

            # get share of population in PUMA areas
            puma_with_pop_gdf["SHARE"] = puma_with_pop_gdf.POP_COUNT / puma_with_pop_gdf.POP_COUNT.sum()

            # add STPUMA to puma_in_cmap gdf
            puma_in_cmap_gdf["STPUMA"] = puma_in_cmap_gdf["PUMACE10"].apply(
                lambda x: int("17" + str(x))
            )

            # get population totals by STPUMA from popsim df
            pop_totals = popsim_df.groupby("STPUMA").size().reset_index(name="count")
            pop_totals.columns = ["STPUMA", "POP_COUNT"]
            puma_with_pop_gdf = puma_in_cmap_gdf.merge(pop_totals, how="left", left_on="STPUMA", right_on="STPUMA")

            # get share of population in PUMA areas
            puma_with_pop_gdf["SHARE"] = puma_with_pop_gdf.POP_COUNT / puma_with_pop_gdf.POP_COUNT.sum()

            # okay now filter PUMS dataset by SERIALNO in popsim
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
                sample = pums_in_popsim_df[pums_in_popsim_df.STPUMA==STPUMA].sample(
                    n=n,
                    replace=False,
                    random_state=0)
                samples.append(sample)

            pums_sample = pd.concat(samples).reset_index(drop=True)

            # post processing
            # there are some encoding discrepancies bt the original PUMS dataset
            # used and thePOLARIS pums dataset generated by the api.

            # Function to pad strings that are numeric
            def pad_numeric_str(val, total_chars):
                return val.zfill(total_chars) if val.isdigit() else val

            # try on POBP
            pums_sample["POBP"] = pums_sample["POBP"].apply(lambda x: pad_numeric_str(x, 3))
            pums_sample["PUMA"] = pums_sample["PUMA"].apply(lambda x: pad_numeric_str(x, 5))

            # this is garbage
            pums_sample["SCHL"] = pums_sample["SCHL"].apply(lambda x: pad_numeric_str(str(int(float(x))), 2))
            pums_sample["CITWP"] = pums_sample["CITWP"].astype(float).astype(int).astype(str)
            pums_sample["MIL"] = pums_sample["MIL"].astype(float).astype(int).astype(str)
            pums_sample["WKHP"] = pums_sample["WKHP"].astype(float).astype(int).astype(str)
            pums_sample["WKWN"] = pums_sample["WKWN"].astype(float).astype(int).astype(str)
            pums_sample["COW"] = pums_sample["COW"].astype(float).astype(int).astype(str)


            # def try_int(val):
            #     try:
            #         # Try converting to int only if string/int-like
            #         if isinstance(val, str) and val.strip().isdigit():
            #             return int(val)
            #         elif isinstance(val, (int, float)) and val == int(val):
            #             return int(val)
            #     except:
            #         pass
            #     return val  # Leave unchanged if conversion fails

            # pums_sample = pums_sample.map(try_int)
            # pums_sample = pums_sample.astype(str)

            return pums_sample

    if source == "FR":
        """
        Read population sample from previous Lyon population synthesis
        https://github.com/eqasim-org/ile-de-france
        """
        if read_from_dataset:
            file_folder = data_folder / "lyon_FD_INDCVI_2021.csv"
            df = pd.read_csv(file_folder, index_col=0, sep=";", dtype=str)

            # add "SERIALNO" field for tracking
            df["SERIALNO"] = df.index

            df["AGE_INT"] = df["AGED"].astype(int)
            df = df[(df["AGE_INT"] > min_age) & (df["AGE_INT"] < max_age)]
            df.drop(labels="AGE_INT", axis=1, inplace=True)

            return df


class _singleAnswerTool(lr.agent.ToolMessage):
    request: str = "singleAnswerResponse"
    purpose: str = """
        To respond with the <TEXT> of the answer that you specify.
        """
    TEXT: int

    @classmethod
    def example(cls):
        return [
            cls(TEXT=45),
            (
                "To respond to the survey question with only one answer",
                cls(TEXT=5)
            )
        ]


class _multipleAnswerTool(lr.agent.ToolMessage):
    request: str = "multipleAnswerResponse"
    purpose: str = """
    To respond with a list of <TEXT> of the answers that apply to your response.
    """
    TEXT: Tuple[int]

    @ classmethod
    def example(cls):
        return [
            cls(TEXT=(4, 7, 12)),
            (
                "I want to response with the keys of the 4 answers that apply to me",
                cls(TEXT=(4, 8, 23, 35))
            ),
            (
                "Only one answer applys to me.",
                cls(TEXT=(5,))
            )
        ]


class _discreteNumericTool(lr.agent.ToolMessage):
    request: str = "discreteNumericResponse"
    purpose: str = """
        To respond with an appropriate numeric <NUMERIC> value when none of the possible responses make sense to apply.
        """
    NUMERIC: int

    @classmethod
    def example(cls):
        return [
            cls(NUMERIC=43),
            (
                "I want to respond with my age of 28",
                cls(NUMERIC=28)
            ),
            (
                "I want to repond with my yearly salary",
                cls(NUMERIC=43_000)
            )
        ]

class _queryRewriteTool(lr.agent.ToolMessage):
    request: str = "queryRewrite"
    purpose: str = """
    Reformulate the survey question so that it can be answered
    by the LADARAG city data API.
    """

    question: str

    @classmethod
    def example(cls):
        return [
            cls(question="How does the Lyon mobility survey define commute trips?")
        ]
    
class _ladaragTool(lr.agent.ToolMessage):
    """
    Marker class — exported so SurveyEngine can import it if needed.
    The actual LADARAG API call is made by SurveyEngine._call_ladarag_api()
    after intercepting a queryRewrite response. The LLM never calls this tool
    directly; it only uses _queryRewriteTool to signal intent.
    """
    request: str = "ladaragQuery"
    question: str

class SurveyAgent(lr.ChatAgent):
    """
    Subclasses Langroid's Chat Agent Class for LLM interfacing and
    logic
    """
    def __init__(self, config: lr.ChatAgentConfig, agent_id: str, bio:str, serial_number: str):
        super().__init__(config)
        """Survey Agent Class

        Args:
            config (lr.ChatAgentConfig): Langroid agent configuration pointing to LLM
            agent_id (str): Agent ID linked to synthesis
            bio (str): Unique contents of system message based on heterogeneous socio-demographic data
            serial_number (str): ID linked to original population synthesis dataset
        """

        # a lot of this logging stuff has been moved to survey logic, remove this eventually
        # record survey responses
        self.agent_id = agent_id
        self.bio = bio
        self.serial_number = serial_number # on PUMS dataset, need to configure for other datasets eventually
        self.responses = []
        self.question_variables = []
        self.question_dtypes = []
        self.dtype_matches = []

        # tool interaction on current question
        self.queued_question: str
        self.possible_responses: Dict[int, str]
        self.queued_keys: List[int]
        self.answer_response = None
        self.answer_types = []

        # survey logging
        self.survey_complete: bool
        self.survey_failed: bool

    def queue_question(self, variable: str, question_package: Dict[str, str | Dict[int, str]], shuffle_response: bool = False):
        """Takes a question/response

        Args:
            variable (str): Question variable
            question_package (Dict): Survey question and possible response dict containing variable encoding and response.
            strict_format (bool):
        """
        self.question_beginning = question_package["question"]
        self.possible_responses = question_package["response"]

        if shuffle_response:
            # shuffle key-value pairs and rebuild the dict
            items = list(self.possible_responses.items())
            random.shuffle(items)
            self.possible_responses = dict(items)

        self.queued_keys = list(self.possible_responses.keys())

        self.question_variables.append(variable)
        self.question_dtypes.append(question_package["dtype"])

        # format question
        if self.question_dtypes[-1] == "TEXT":
            self.queued_question = f"{self.question_beginning} Available options: " + "; ".join(
                f"{key}: {value}" for key, value in self.possible_responses.items()
                )
        elif self.question_dtypes[-1] == "NUMERIC":
            self.queued_question = f"{self.question_beginning} Please provide a numeric response or select an alternative: " + "; ".join(
                f"{key}: {value}" for key, value in self.possible_responses.items()
                )

    def llm_response(self, message: Optional[str | ChatDocument] = None) -> Optional[ChatDocument]:
        return super().llm_response(message)

    def ask_question(self):
        self.llm_response(self.queued_question)

    def singleAnswerResponse(self, msg: _singleAnswerTool) -> str:
        # return answer if exists in queued keys
        self.dtype_matches.append("TEXT" == self.question_dtypes[-1])
        return str(msg.TEXT if msg.TEXT in self.queued_keys else None)

    def multipleAnswerResponse(self, msg: _multipleAnswerTool):
        self.dtype_matches.append("TEXT" == self.question_dtypes[-1])
        return str(
            _multipleAnswerTool.TEXT if all(
                key in self.queued_keys for key in _multipleAnswerTool.TEXT) else None)

    def discreteNumericResponse(self, msg: _discreteNumericTool):
        self.dtype_matches.append("NUMERIC" == self.question_dtypes[-1])
        return str(msg.NUMERIC if msg.NUMERIC not in self.queued_keys else None)


def build_agents(config_folder:str, n: int, source: str, subsample: int | None = None, **kwargs):
    model_config, synth_conf, _, _ = load_config(config_folder)
    population_sample = synthesize_population(config_folder=config_folder, n_sample=subsample, source=source, min_age=18, max_age=65)

    assert isinstance(population_sample, pd.DataFrame)

    if source == "US":
        person = process_pums_data(config_folder=config_folder)
        ploc = puma_locations(config_folder)
    if source == "FR":
        person = process_insee_census(config_folder=config_folder)
        ploc = None

    MsgGen = SystemMessageGenerator(config_folder, "SystemMessage.j2", **kwargs)

    # synthesis configuration vars
    sim_year = synth_conf.get("sim_year")
    header = synth_conf.get("system_message_header")
    footer = synth_conf.get("system_message_footer")

    system_messages = []
    serial_numbers = [] # link to person dataset

    attribute_descriptions = get_attribute_descriptions(person)
    for i, individual in population_sample.iterrows():
        individual_attributes = attribute_decoder_dict(individual.to_dict(), person)
        system_message = MsgGen.write_system_message(
            **individual_attributes,
            **attribute_descriptions,
            ploc=ploc,
            YEAR=sim_year)

        system_messages.append(system_message)
        serial_numbers.append(individual["SERIALNO"])

    llm_config = lm.OpenAIGPTConfig(**model_config)
    agents = []
    for i, zipped_content in enumerate(zip(system_messages[0:subsample], serial_numbers[0:subsample])):
        system_message, serial_number = zipped_content
        agent_config = lr.ChatAgentConfig(
            name=f"Agent_{i}",
            llm=llm_config,
            system_message= header + system_message + footer,   # system message configuration
            use_tools=True,                                     # - could have more in the future
            use_functions_api=False)
        agent = SurveyAgent(config=agent_config, agent_id = i, bio = system_message, serial_number = serial_number)
        agent.enable_message(_singleAnswerTool)
        agent.enable_message(_multipleAnswerTool)
        agent.enable_message(_discreteNumericTool)
        agent.enable_message(_queryRewriteTool)
        # NOTE: _ladaragTool is NOT enabled on the agent.
        # The LLM signals context intent via _queryRewriteTool.
        # SurveyEngine then calls LADARAG directly and re-prompts the LLM.
        agents.append(agent)

    """
    Must manually change tool use formatting instructions to FRENCH
    https://github.com/langroid/langroid/blob/main/langroid/agent/chat_agent.py
    lines 178 and 180.
    """

    # these must be changed to reflect new agent tools
    fr_system_tool_instructions = '=== DIRECTIVES SUR L\'UTILISATION DE CERTAINS OUTILS/FONCTIONS ===\n            TOOL: singleAnswerResponse:\n                        DIRECTIVES: \n        IMPORTANT: Lors de l\'utilisation de cet outil ou de tout autre outil/fonction, vous DEVEZ inclure un \n        `request` champ et le définir égal au NOM DE L\'OUTIL/FONCTION que vous avez l\'intention d\'utiliser.\n\n\n\n\nTOOL: multipleAnswerResponse:\n                        DIRECTIVES: \n        IMPORTANT: Lors de l\'utilisation de cet outil ou de tout autre outil/fonction, vous DEVEZ inclure un \n        `request` champ et le définir égal au NOM DE L\'OUTIL/FONCTION que vous avez l\'intention d\'utiliser.\n\n\n\n\nTOOL: discreteNumericResponse:\n                        DIRECTIVES: \n        IMPORTANT: Lors de l\'utilisation de cet outil ou de tout autre outil/fonction, vous DEVEZ inclure un \n        `request` champ et le définir égal au NOM DE L\'OUTIL/FONCTION que vous avez l\'intention d\'utiliser.\n\n\n\n'

    fr_system_tool_format_instructions = '\n=== TOUS LES OUTILS DISPONIBLES et LEURS INSTRUCTIONS DE FORMAT ===\nVous avez accès aux OUTILS suivants pour accomplir votre tâche :\n\nTOOL: singleAnswerResponse\n            OBJECTIF: \n        Répondre avec le <TEXT> de la réponse que vous spécifiez.\n\n            FORMAT JSON: {\n    "type": "object",\n    "properties": {\n        "request": {\n            "default": "singleAnswerResponse",\n            "type": "string"\n        },\n        "TEXT": {\n            "type": "integer"\n        }\n    },\n    "required": [\n        "TEXT",\n        "request"\n    ],\n    "request": {\n        "enum": [\n            "singleAnswerResponse"\n        ],\n        "type": "string"\n    }\n}\n\n\n\nTOOL: multipleAnswerResponse\n            OBJECTIF: \n    Répondre avec une liste de <TEXT> des réponses qui s\'appliquent à votre réponse.\n\n            FORMAT JSON: {\n    "type": "object",\n    "properties": {\n        "request": {\n            "default": "multipleAnswerResponse",\n            "type": "string"\n        },\n        "TEXT": {\n            "type": "array",\n            "minItems": 1,\n            "maxItems": 1,\n            "items": [\n                {\n                    "type": "integer"\n                }\n            ]\n        }\n    },\n    "required": [\n        "TEXT",\n        "request"\n    ],\n    "request": {\n        "enum": [\n            "multipleAnswerResponse"\n        ],\n        "type": "string"\n    }\n}\n\n\n\nTOOL: discreteNumericResponse\n            OBJECTIF: \n        Répondre avec une valeur <NUMERIC> appropriée lorsque aucune des réponses possibles n’a de sens à appliquer.\n\n            FORMAT JSON: {\n    "type": "object",\n    "properties": {\n        "request": {\n            "default": "discreteNumericResponse",\n            "type": "string"\n        },\n        "NUMERIC": {\n            "type": "integer"\n        }\n    },\n    "required": [\n        "NUMERIC",\n        "request"\n    ],\n    "request": {\n        "enum": [\n            "discreteNumericResponse"\n        ],\n        "type": "string"\n    }\n}\n\n\n\nLorsqu’un des OUTILS ci-dessus est applicable, vous devez exprimer votre \ndemande par "TOOL :" suivi de la requête dans le format ci-dessus.\n'

    if source == "FR":
        for agent in agents:
            agent.system_tool_instructions = fr_system_tool_instructions
            agent.system_tool_format_instructions = fr_system_tool_format_instructions

    return agents, population_sample


if __name__ == "__main__":
    build_agents("configs/Chicago", 50, 3)