from synthesize import load_config
from preprocess import generate_questions
from survey import AgentResponsePackage
from pathlib import Path
from dataclasses import asdict
import copy
import pandas as pd
import json
import re
import matplotlib.pyplot as plt
import colorcet as cc

class ProcessSurveyResponse:
    def __init__(self, config_folder: str, batch_size: int, RUN_FOLDER: str, source: str, date_str: str):
        self.data_path = Path(config_folder) / "data"
        self.batch_size = batch_size
        self.n_batches = 0
        self.batches_written = 1
        self.RUN_FOLDER = RUN_FOLDER
        self.source = source
        self.date_str = date_str
        self._prepare_dataset()

    def _prepare_dataset(self):
        if self.source == "US":
            self.multiple_choice_cols = ["NOGOWHY2", "TRAVELDATAMODE", "DTYPE"]
            self.ground_truth_df = pd.read_csv(self.data_path / "person.csv", low_memory=False)
        else:
            self.multiple_choice_cols = []
            questions_df = pd.read_csv(self.data_path / "../questions.csv", header=None, index_col=0).T
            self.ground_truth_df = questions_df

        ground_truth_cols = self.ground_truth_df.columns
        self.synthetic_columns = ["agent_id", "serial_number", "agent_bio", "intro"]
        self.synthetic_columns.extend(ground_truth_cols)
        self.synthetic_dataset = pd.DataFrame(columns=self.synthetic_columns)
        self.batch_dataset = copy.deepcopy(self.synthetic_dataset)
        self.synthetic_asdict = []
        self.batch_asdict = []

    def serialize_response(self, agent_response: AgentResponsePackage):
        response_dict = asdict(agent_response)
        response_cols = [col.lower() for col in response_dict["logic_flow"]]
        new_row = {}

        # get agent id and system_message
        new_row["agent_id"]       = agent_response.agent_id
        new_row["serial_number"]  = agent_response.serial_number
        new_row["agent_bio"]      = agent_response.agent_bio

        # Loop through the logic flow and encoded responses to build the new row
        for col, val in zip(response_cols, response_dict["encoded_responses"]):
            # Handle multiple choice columns which might be lists of ints
            if col in self.multiple_choice_cols:
                if isinstance(val, list):
                    # Convert each element in the list to an integer, if possible
                    new_row[col] = [self._coerce_to_int(x) for x in val]
                else:
                    # If it's not a list, try to convert it to an int and wrap it in a list
                    new_row[col] = [self._coerce_to_int(val)]
            else:
                # For other types of values, handle coercion based on type
                if isinstance(val, str):
                    val = val.replace('\n', '').replace('\r', '')
                    new_row[col] = val  # Ensure it's a string
                else:
                    new_row[col] = self._coerce_to_int(val)  # Coerce to integer if possible

        # serialize result to dataset
        self.synthetic_dataset = pd.concat([self.synthetic_dataset, pd.DataFrame([new_row])], ignore_index=True)
        self.synthetic_asdict.append(response_dict)

        # batch dataset
        self.batch_dataset = pd.concat([self.batch_dataset, pd.DataFrame([new_row])], ignore_index=True)
        self.batch_asdict.append(response_dict)

        self._batch_write_results()

    def _coerce_to_int(self, value):
        """
        Helper function to attempt to coerce a value into an integer.
        Returns the value if coercion is not possible.
        """
        try:
            # If the value is already an integer, return it directly
            if isinstance(value, int):
                return value
            # If it's a string that can be converted to an integer, try to do that
            return int(value)
        except (ValueError, TypeError):
            # Return the value as is if it cannot be coerced into an integer
            return value

    def write_results(self, RUN_FOLDER, date_str) -> bool | str:
        # write csv and json this may fail
        write_success = True
        try:
            self.synthetic_dataset.to_csv(Path(RUN_FOLDER) / "_".join((date_str, "results.csv")), index=False)
        except Exception as e:
            write_success = e

        try:
            with open(Path(RUN_FOLDER) / "_".join((date_str, "results.json")), "w") as f:
                json.dump(self.synthetic_asdict, f, indent=4)
        except Exception as e:
            write_success = e

        return write_success

    def _batch_write_results(self) -> None:
        # write csv and json this may fail
        if self.n_batches % self.batch_size == 0:
            try:
                self.batch_dataset.to_csv(Path(self.RUN_FOLDER) / f"batch_{self.batches_written}_{self.date_str}_results.csv", index=False)
            except Exception as e:
                print(e)

            try:
                with open(Path(self.RUN_FOLDER) / f"batch_{self.batches_written}_{self.date_str}_results.json", "w") as f:
                    json.dump(self.batch_asdict, f, indent=4)
            except Exception as e:
                print(e)

            # reset batch datasets
            self.batches_written += 1
            self.batch_dataset = self.batch_dataset[0:0]
            self.batch_asdict = []

        # iterate n_batches written
        self.n_batches += 1


class ResultsWriter:
    def __init__(self, config_folder: str, RUN_FOLDER: str, source:str="US"):
        self.config_folder = config_folder
        self.data_path = Path(config_folder) / "data"
        self.RUN_PATH = Path(RUN_FOLDER)
        self.source = source
        _, _, _, self.analysis_conf = load_config(config_folder=config_folder)
        self._load_datasets()
        self._clean_test_datasets()
        self._set_plotting_format()


    def _load_datasets(self):
        # get timestamp from run folder name
        SEP = "_"
        self.timestamp = SEP.join(self.RUN_PATH.name.split(SEP)[-2:])

        # check if sim is complete
        if any(self.RUN_PATH.glob(self.timestamp+"_results.*")):
            self.test_dataset = pd.read_csv(self.RUN_PATH / (self.timestamp+"_results.csv"))
        else:
            batches = self.RUN_PATH.glob("batch_*_results.csv")
            self.test_dataset = pd.concat([pd.read_csv(batch) for batch in batches], ignore_index=True)

        # load true dataset
        if self.source == "US":
            self.true_dataset = pd.read_csv(self.data_path / "person.csv", low_memory=False)

    def _clean_test_datasets(self):
        """
        This is a bad work around fix to properly format bad LLM responses. Need to fix this eventually.
        """
        # get survey variables
        self.questions = generate_questions(config_folder=self.config_folder, source=self.source)
        self.survey_vars = list(self.questions.keys())
        self.survey_vars_lower = [var.lower() for var in self.survey_vars]

        # drop all Nan cols
        self.test_dataset = self.test_dataset.dropna(axis=1, how="all")

        # convert all cols to int except multiple choice cols
        multiple_choice_cols_lower = [var.lower() for var in self.analysis_conf.get("multiple_choice")]
        exclude_cols_lower = [var.lower() for var in self.analysis_conf.get("exclude")]
        test_cols = self.test_dataset.columns
        int_cols = []
        for col in test_cols:
            if \
                col in self.survey_vars_lower and \
                col not in multiple_choice_cols_lower and \
                col not in exclude_cols_lower:
                    int_cols.append(col)
                    self.test_dataset[col] = self.test_dataset[col].apply(_extract_first_int)
        self.test_dataset[int_cols] = self.test_dataset[int_cols].astype("Int64")

    def _group_dataset(self, var: str, dataset: pd.DataFrame) -> pd.DataFrame:
        """
        get sums and percentages of responded survey value
        exclude var > 0 for innapropriate vars
        """
        grouped = dataset.groupby(by=var)[var].value_counts().reset_index()
        grouped = grouped[grouped[var] > 0].reset_index(drop=True)
        grouped["share"] = grouped["count"] / grouped["count"].sum()
        return grouped

    def _set_plotting_format(self):
        self.colors = cc.glasbey

    def generate_figure(self, test: pd.DataFrame, true: pd.DataFrame, var: str, how="share"):
        """Generates grouped figure of results

        Args:
            test (pd.DataFrame): test (synthetic) dataset generated from _group_dataset()
            true (pd.DataFrame): true dataset
            var (str): survey variable to be plotted
            how (str, optional): Display "share" percentage or "count" value counts. Defaults to "share".
        """

        # align vals on test dataset with all possible from true dataset
        vals = set(true[var])
        test = test.set_index(var).reindex(vals, fill_value=0)
        true = true.set_index(var)

        # set up plot
        fig, ax = plt.subplots(figsize=(12,6))

        # transform and concat for plotting
        if how == "share":
            true["percent"] = true["share"] * 100
            true = true.drop(columns=["count", "share"])
            test["percent"] = test["share"] * 100
            test = test.drop(columns=["count", "share"])

            plotting_df = pd.concat([true, test], axis=1).T

            plotting_df.index = ["Survey Data", "LLM Synthetic"]

        # rename
        ax = plotting_df.plot.bar(
            stacked=True,
            ax=ax,
            color=self.colors,

            width=0.90,
            rot=0
            )

        # format legend
        handles, labels = ax.get_legend_handles_labels()
        ax.legend(
            handles=handles,
            labels=[self.questions.get(var.upper()).get("response").get(int(label)) for label in labels],
            )
        plt.tight_layout()


def _extract_first_int(x):
    if isinstance(x, str):
        m = re.search(r"\d+", x)
        if m:
            return int(m.group ())
        else:
            return None
    return x


def main():
    pass

if __name__ == "__main__":
    main()