import pandas as pd
import geopandas as gpd
import re
from geopandas import GeoDataFrame
from shapely import Point
import json
import yaml
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import langroid as lr
import langroid.language_models as lm
from langroid.agent.chat_agent import ChatDocument
from preprocess import *
import requests


def synthesize_population(config_yaml: str | Path, n_sample:int, source:str="US", min_age: int|None = None, max_age: int|None = None, read_from_dataset: bool|None = True, random_state=0) -> pd.DataFrame | None:
    """
    Returns a spatially proportional sample of the PUMS dataset based on CMAP My Daily Travel Survey respondent sample.
    """
    config_yaml = Path(config_yaml)
    config_dir = config_yaml.parent

    with open(config_yaml, 'r') as f:
        config = yaml.safe_load(f)

    data_folder = config_dir / "data"
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

            # get populationsim synthetic population
            popsim_df = pd.read_csv(config_dir / "data/populationsim/output/synthetic_persons.csv")

            # get PUMS dataset from POLARIS
            pums_df = pd.read_csv(config_dir / "data/populationsim/data/pums_person_chicago.csv", dtype=str)

            # get PUMS PUMA geography
            # https://catalog.data.gov/dataset/tiger-line-shapefile-2019-2010-state-illinois-2010-census-public-use-microdata-area-puma-state-
            puma_gdf = gpd.read_file(config_dir / "data/tl_2019_17_puma10.shp")

            # get CMAP planning area
            # https://datahub.cmap.illinois.gov/datasets/4834d52310d24e56a0300898a0cb23bc_0/explore
            cmap_gdf = gpd.read_file(config_dir / "data/Facility_Planning_Areas_2016.shp")

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
