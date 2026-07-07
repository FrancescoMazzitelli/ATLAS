import geopandas as gpd
import random
import yaml
from pathlib import Path
import json
import argparse
from pandas import DataFrame
from geopandas import GeoDataFrame


def _to_crs(from_gdf: GeoDataFrame, to_gdf:GeoDataFrame)->GeoDataFrame:
    return to_gdf.to_crs(from_gdf.crs)


def _time_to_seconds(time_str):
    h, m = map(int, time_str.split(':'))
    return h * 3600 + m * 60


class LocationGenerator:
    def __init__(self, config_yaml, random_seed: int | None = 1):
        with open(config_yaml, 'r') as f:
            self.config = yaml.safe_load(f)
        self.data_dir = Path(self.config["data"]["base_path"])
        self._rng = random.Random(random_seed)


    def build_locations(self, population_df: DataFrame):
        # load spatial
        locations = gpd.read_file(
            next(
                self.data_dir.glob("**/Building Footprints_*/*.shp")
            ))
        zones = gpd.read_file(
            next(
                self.data_dir.glob("*_puma10.shp")
            ))
        cbd_boundary = gpd.read_file(
            next(
                self.data_dir.glob("**/Central_Business_District_*/*.shp")
            ))
        boundary = gpd.read_file(
            next(
                self.data_dir.glob("**/Boundaries - City_*/*.shp")
            ))

        # convert crs
        zones =        _to_crs(locations, zones)
        cbd_boundary = _to_crs(locations, cbd_boundary)
        boundary =     _to_crs(locations, boundary)

        # filter to project boundary
        self.clipping_area = boundary.union_all()
        self.zones = zones[(zones.within(self.clipping_area)) | (zones.intersects(self.clipping_area))]
        self.locations = locations[locations.within(self.clipping_area)]
        self.cbd_locations = locations[locations.within(cbd_boundary.boundary.union_all())]

        # assign PUMA to locations
        self.locations = gpd.sjoin(self.locations, self.zones[["PUMACE10", "geometry"]], how="left")
        self.locations["PUMA"] = self.locations["PUMACE10"].apply(lambda x: str(int(x))[1:])
    