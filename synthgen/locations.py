import pandas as pd
import geopandas as gpd
import numpy as np
import random
import yaml
import json
import shapely
from pathlib import Path
from pandas import DataFrame
from geopandas import GeoDataFrame


def _to_crs(from_gdf: GeoDataFrame, to_gdf:GeoDataFrame)->GeoDataFrame:
    return to_gdf.to_crs(from_gdf.crs)


def _time_to_seconds(time_str):
    h, m = map(int, time_str.split(':'))
    return h * 3600 + m * 60


def _point_array_to_tuples(shapes: gpd.GeoSeries):
    location_points = shapes.geometry.centroid
    return list(zip(shapely.get_x(location_points), shapely.get_y(location_points)))


class LocationGenerator:
    def __init__(self, config_yaml, random_seed: int | None = 1):
        with open(config_yaml, 'r') as f:
            self.config = yaml.safe_load(f)
        self.data_dir = Path(self.config["data"]["base_path"])
        self._rng = random.Random(random_seed)


    def build_locations(self, run_dir: Path | str | None = None, rebuild: bool = False):
        run_dir = Path(run_dir) if run_dir is not None else None
        files = {
            "locations": "locations.shp",
            "zones": "zones.shp",
            "cbd_locations": "cbd_locations.shp",
            "clipping_area": "clipping_area.shp",
        }

        if (run_dir is not None and all((run_dir / f).exists() for f in files.values())) and not rebuild:
            self.locations = gpd.read_file(run_dir / files["locations"])
            self.zones = gpd.read_file(run_dir / files["zones"])
            self.cbd_locations = gpd.read_file(run_dir / files["cbd_locations"])
            self.clipping_area = gpd.read_file(run_dir / files["clipping_area"]).geometry.iloc[0]
            return
        
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
        self.cbd_locations = locations[locations.intersects(cbd_boundary.boundary.union_all())]

        # assign PUMA to locations
        self.locations = gpd.sjoin(self.locations, self.zones[["PUMACE10", "geometry"]], how="left")
        self.locations["PUMA"] = self.locations["PUMACE10"].apply(lambda x: str(int(x))[0:])
    
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            self.locations.to_file(run_dir / files["locations"])
            self.zones.to_file(run_dir / files["zones"])
            self.cbd_locations.to_file(run_dir / files["cbd_locations"])
            gpd.GeoDataFrame(geometry=[self.clipping_area], crs=self.locations.crs).to_file(
                run_dir / files["clipping_area"]
            )


    def assign_locations(self, run_dir: str | Path, random_seed: int | None = None):
        all_locations = _point_array_to_tuples(self.locations)
        cbd_locations = _point_array_to_tuples(self.cbd_locations)
        location_pumas = self.locations.PUMA.unique()

        puma_location_map  = dict()
        for puma in location_pumas:
            locations = _point_array_to_tuples(self.locations[self.locations.PUMA == puma])
            puma_location_map[puma] = locations

        def _sample_by_location(location, puma, rng):
            if location == "HOME" and puma in puma_location_map.keys():
                candidates = puma_location_map[puma]
                idx = rng.integers(len(candidates))
                coords = candidates[idx]
            elif location == "WORK" or location == "SCHOOL":
                idx = rng.integers(len(cbd_locations))
                coords = cbd_locations[idx]
            else:
                idx = rng.integers(len(all_locations))
                coords = all_locations[idx]
            return coords

        rng = np.random.default_rng(random_seed)

        run_dir = Path(run_dir)
        agents_dir =     run_dir / "agents.jsonl"
        population_dir = run_dir / "population.csv"
        
        population_df = pd.read_csv(population_dir)

        with open(agents_dir, "r") as f:
            agents = [json.loads(line) for line in f if line.strip()]
        agents = [agent for agent in agents if "itinerary" in agent.keys()]

        agent_indexes = list()
        departures_sec = list()
        trip_ids = list()
        origins = list()
        destinations = list()
        pumas = list()
        o_xs = list()
        o_ys = list()
        d_xs = list()
        d_ys = list()
        
        for agent in agents:
            # departure times
            departures = agent["itinerary"]["departure_times"]
            departures = [_time_to_seconds(departure) for departure in departures]
            departures_sec.extend(departures)

            # trip ids and departures
            trip_ids.extend(range(0, len(departures)))
            n_departures = len(departures)
            agent_idx = agent["agent_idx"]
            agent_indexes.extend([agent_idx] * n_departures)

            # PUMA
            puma = str(population_df[population_df.agent_id == agent_idx].PUMA.values[0])
            pumas.extend([puma] * n_departures)

            # origin/destination, i indexes previous location
            locations = agent["itinerary"]["locations"]
            for i, destination in enumerate(locations[1:]):
                # get origins and destinations
                origin = locations[i]
                origins.append(origin)
                destinations.append(destination)
                
                o_x, o_y = _sample_by_location(origin, puma, rng)
                d_x, d_y = _sample_by_location(destination, puma, rng)
                o_xs.append(o_x)
                o_ys.append(o_y)
                d_xs.append(d_x)
                d_ys.append(d_y)

        data = {
            "agent_idx":     agent_indexes,
            "trip_id":       trip_ids,
            "origin":        origins,
            "destination":   destinations,
            "o_x": o_xs,
            "o_y": o_ys,
            "d_x": d_xs,
            "d_y": d_ys,
            "departure_sec": departures_sec
        }

        self.trips = pd.DataFrame(data)


if __name__ == "__main__":
    run_dir = Path("/home/isalvador/git/ATLAS/synthgen/run/instr_small")
    lg = LocationGenerator("config.yaml")
    lg.build_locations(run_dir, rebuild=False)
    lg.assign_locations(run_dir, random_seed=1)
    lg.trips.to_csv(run_dir / "trips.csv", index=None)

    