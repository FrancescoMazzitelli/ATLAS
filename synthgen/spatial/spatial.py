import osmnx as ox
import geopandas as gpd
from pathlib import Path
import os
from geopandas import GeoDataFrame
import warnings

cache_folder = Path("../../cache")

ox.settings.cache_folder = cache_folder

def _geom_filter(gdf: GeoDataFrame, geom_type: str) -> GeoDataFrame:
    """
    Quick Filter for geometry types i.e. Point, Polygon, and LineString
    """
    return gdf[gdf.geometry.geom_type == geom_type]

def get_spatial_data_by_location(location: str, network_type: str = "drive"):

    # bounding area - city or county
    print("Getting location boundary")
    gdf = ox.geocode_to_gdf(location)
    polygon = gdf["geometry"].iloc[0]

    # get networks
    print("Getting network")
    G_drive = ox.graph_from_polygon(polygon=polygon, network_type="drive")
    G_walk = ox.graph_from_polygon(polygon=polygon, network_type="walk")
    drive_nodes, drive_edges = ox.graph_to_gdfs(G_drive)
    walk_nodes, walk_edges = ox.graph_to_gdfs(G_walk)

    # transit
    print("Getting transit data...")
    stops = ox.features_from_place(
        location,
        tags={"public_transport": "platform"}
    )

    print("Rail")
    rail_lines = ox.features_from_place(
        location,
        tags={"railway": ["subway", "light_rail", "tram"]}
    )

    print("Bus")
    bus_routes = ox.features_from_place(
        location,
        tags={"highway": "bus_stop"}
    )

    # save to cache, geometry type filtering, ignore warings for column truncation
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        print("Saving to shapefiles.")
        location_name = location.split(sep=",")[0]
        cache_subfolder = cache_folder / "shapefiles" / location_name
        os.makedirs(cache_subfolder, exist_ok=True)
        gdf.to_file(            cache_subfolder / "boundary.shp")
        _geom_filter(drive_nodes, "Point"               ).to_file(cache_subfolder / "drive_nodes.shp")
        _geom_filter(drive_edges, "LineString"          ).to_file(cache_subfolder / "drive_edges.shp")
        _geom_filter(walk_nodes, "Point"                ).to_file(cache_subfolder / "walk_nodes.shp")
        _geom_filter(walk_edges, "LineString"           ).to_file(cache_subfolder / "walk_edges.shp")

        # stops composed of Point, Polygon and LineString
        for geometry_type in stops.geometry.geom_type.unique():
            stops_subset = stops[stops.geometry.geom_type == geometry_type]
            stops_subset.to_file(                                 cache_subfolder / f"stops_{geometry_type.lower()}.shp")

        _geom_filter(rail_lines, "LineString"           ).to_file(cache_subfolder / "rail_lines.shp")
        _geom_filter(bus_routes, "Point"                ).to_file(cache_subfolder / "bus_routes.shp")

if __name__ == "__main__":
    location = "Chicago, Illinois, US"
    get_spatial_data_by_location(location)



