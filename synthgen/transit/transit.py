import requests
import dotenv
import pandas as pd
import gtfs_kit as gk
import os
from pandas import DataFrame
from geopandas import GeoDataFrame
from requests import Response
from typing import Dict
from pathlib import Path

ENV_PATH          = "../../.env"
CACHE_PATH        = "../../cache"
GTFS_PATH         = "../../cache/gtfs/google_transit.zip"
BUS_TRACKER_KEY   = dotenv.get_key(ENV_PATH, "BUS_TRACKER_KEY")
TRAIN_TRACKER_KEY = dotenv.get_key(ENV_PATH, "BUS_TRACKER_KEY")
BUS_ROUTES_API    = dotenv.get_key(ENV_PATH, "BUS_ROUTES_API")
ALERTS_API        = dotenv.get_key(ENV_PATH, "ALERTS_API")


def bus_routes_url():
    assert BUS_ROUTES_API is not None
    url = BUS_ROUTES_API + f"?key={BUS_TRACKER_KEY}&format=json"
    return url


def CTA_alerts_url():
    return ALERTS_API


def default_request(url, params: Dict[str, str]|None = None) -> Response|None:
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        return response

    except requests.exceptions.Timeout:
        print("Request timed out")
    except requests.exceptions.ConnectionError:
        print("Network error")
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
    except requests.exceptions.RequestException as e:
        # catches ALL requests errors as a fallback
        print(f"Something went wrong: {e}")

    return None


def get_alerts_df() -> DataFrame:
    r = default_request(
        CTA_alerts_url(),
        params={"outputType":"JSON"}
    )

    assert r is not None
    alerts = r.json()["CTAAlerts"]["Alert"]
    for alert in alerts:
        assert isinstance(alert, dict)
        service = alert.get("ImpactedService", {}).get("Service")
        if isinstance(service, dict):
            alert["ImpactedService"]["Service"]=[service]

    alerts_df = pd.json_normalize(
        alerts, record_path=["ImpactedService", "Service"],
        meta=[
            'AlertId',
            'Headline',
            'ShortDescription',
            'FullDescription',
            'SeverityScore',
            'SeverityCSS',
            'Impact',
            'EventStart',
            'EventEnd',
            'TBD',
            'MajorAlert',
            'AlertURL',
            'ttim',
            'GUID'
        ],
    )

    alerts_df["SS_int"] = alerts_df["SeverityScore"].astype(int)

    return alerts_df


def main() -> None:
    feed = gk.read_feed(GTFS_PATH, dist_units="m")

    alerts_df = get_alerts_df()
    bus_alerts = alerts_df[alerts_df["ServiceType"]=="B"]

    all_stops=[]
    for _, alert in bus_alerts.iterrows():
        stops = gk.get_stops(feed=feed, route_ids=[alert.ServiceId], as_gdf=True)

        stops["route_id"] = alert.ServiceId
        stops["alert_id"] = alert.AlertId
        stops["headline"] = alert.Headline
        stops["short_desc"] = alert.ShortDescription
        stops["cdata"] = alert["ServiceURL.#cdata-section"]
        stops["impact"] = alert.Impact
        stops["severity"] = alert.SS_int
        stops["start"] = alert.EventStart
        stops["end"] = alert.EventEnd

        all_stops.append(stops)

    bus_stop_alerts=pd.concat(all_stops, axis=0).reset_index(drop=True)

    bus_stop_alerts["start"] = pd.to_datetime(bus_stop_alerts["start"], format="mixed")
    bus_stop_alerts["end"] = pd.to_datetime(bus_stop_alerts["end"], format="mixed")

    bus_stop_alerts["t_start"] = \
        bus_stop_alerts["start"].dt.hour * 3600 + \
        bus_stop_alerts["start"].dt.minute * 60 + \
        bus_stop_alerts["start"].dt.second

    bus_stop_alerts["t_end"] = \
        bus_stop_alerts["end"].dt.hour * 3600 + \
        bus_stop_alerts["end"].dt.minute * 60 + \
        bus_stop_alerts["end"].dt.second

    EVENT_PATH = Path(CACHE_PATH)/"events"
    os.makedirs(EVENT_PATH, exist_ok=True)

    assert isinstance(bus_stop_alerts, GeoDataFrame)
    bus_stop_alerts.to_file(EVENT_PATH/"bus_stop_alerts.shp")

if __name__ == "__main__":
    main()