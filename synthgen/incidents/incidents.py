import requests
import dotenv
import os
import json
import shutil
import copy
import datetime
from pathlib import Path
from typing import Dict, List
from requests import Response

ENV_PATH = dotenv.find_dotenv()

# API vars
BASE_URL = dotenv.get_key(ENV_PATH, "TOMTOM_BASE_URL")
API_KEY = dotenv.get_key(ENV_PATH, "TOMTOM_API_KEY")
BBOX = dotenv.get_key(ENV_PATH, "CHICAGO_BBOX")

# SCRAPER vars
SCRAPER_PATH = dotenv.get_key(ENV_PATH, "SCRAPER_PATH")


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


def _incidents_params() -> Dict[str,str]:
    params = {}

    params["bbox"] = BBOX
    params["key"] = API_KEY
    params["fields"] = "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers,timeValidity,probabilityOfOccurrence,numberOfReports,lastReportTime,tmc{countryCode,tableNumber,tableVersion,direction,points{location,offset}}}}}"
    params["language"] = "en-US"


    return params


def get_incidents_response() -> Response|None:
    params = _incidents_params()
    resp = default_request(BASE_URL, params=params)
    return resp


def _init_scraper():
    # make cache/scraper/cache folders
    assert SCRAPER_PATH is not None
    SCRAPER_CACHE_PATH = Path(SCRAPER_PATH) / "cache"
    if os.path.exists(SCRAPER_CACHE_PATH):
        print("Scraper folders exists")
    else:
        print("Creating scraper folders")
        os.makedirs(SCRAPER_CACHE_PATH, exist_ok=True)

    return SCRAPER_CACHE_PATH


def _write_incidents(write_path: os.PathLike|str, file_name:str, incidents: List[dict])->os.PathLike:
    file_path = Path(write_path) / file_name
    with open(file_path, "w") as fp:
        json.dump(incidents, fp, indent=4)

    return file_path


def backup_aggregated_incidents(SCRAPER_CACHE_PATH: os.PathLike, AGGREGATED_INCIDENTS_PATH: os.PathLike)->None:
    BACKUP_PATH = Path(SCRAPER_CACHE_PATH) / "aggregated_incidents_backup.json"
    shutil.copy(AGGREGATED_INCIDENTS_PATH, BACKUP_PATH)


def update_aggregated_incidents(aggregated_incidents: List[dict], latest_incidents: List[dict]):
    # back up original aggregated incidents
    backup_incidents = copy.deepcopy(aggregated_incidents)
    bad_update = False

    # incident IDs
    aggregated_incident_ids = [incident["properties"]["id"] for incident in aggregated_incidents]
    latest_incident_ids = [incident["properties"]["id"] for incident in latest_incidents]

    # check new incidents
    new_ids = list(set(latest_incident_ids) - set(aggregated_incident_ids))

    # get ID-mapped latest incidents
    id_mapped_latest_incidents = {
        incident["properties"]["id"]: incident \
        for incident in latest_incidents
    }

    # update loop
    n_updated = 0
    for index, incident in enumerate(aggregated_incidents):
        incident_id = incident["properties"]["id"]
        if incident_id in id_mapped_latest_incidents.keys():
            new_incident = id_mapped_latest_incidents.get(incident_id)
            assert new_incident is not None

            old_endTime_str = incident["properties"].get("endTime")
            new_endTime_str = new_incident["properties"].get("endTime")

            # old_endTime_dt = datetime.datetime.fromisoformat(old_endTime_str)

            update = False

            if old_endTime_str is None:
                update = True
                n_updated += 1
            else:
                pass

            if update:
                aggregated_incidents[index] = new_incident

    # add new updates
    new_incidents = [id_mapped_latest_incidents[id] for id in new_ids]
    aggregated_incidents.extend(new_incidents)


    print(f"\nNew incidents: {len(new_ids)}")
    print(f"Updated incidents: {n_updated}")
    print(f"New updated aggregated incidents: {len(aggregated_incidents)}")

    return aggregated_incidents


def scrape():
    SCRAPER_CACHE_PATH = _init_scraper()
    assert SCRAPER_PATH is not None
    AGGREGATE_INCIDENTS_PATH = Path(SCRAPER_PATH) / "aggregated_incidents.json"

    # get and save latest incidents
    print("\nPulling from API")
    incidents_response = get_incidents_response()
    assert incidents_response is not None
    latest_incidents = incidents_response.json()["incidents"]

    print(f"Writing latest incidents ({len(latest_incidents)} incidents)")
    LATEST_INCIDENTS_PATH = _write_incidents(SCRAPER_CACHE_PATH, "latest_incidents.json", latest_incidents)

    # read latest aggregated results or create them
    if not os.path.exists(AGGREGATE_INCIDENTS_PATH):
        print("No aggregated incidents exist, copying from latest")
        shutil.copy(LATEST_INCIDENTS_PATH, AGGREGATE_INCIDENTS_PATH)
        aggregated_incidents = latest_incidents
    else:
        with open(AGGREGATE_INCIDENTS_PATH, "r") as fp:
            aggregated_incidents = json.load(fp)
            print(f"Loading saved aggregated incidents ({len(aggregated_incidents)} incidents)")

    backup_aggregated_incidents(SCRAPER_CACHE_PATH, AGGREGATE_INCIDENTS_PATH)
    new_aggregated_incidents = update_aggregated_incidents(aggregated_incidents, latest_incidents)

    _write_incidents(SCRAPER_PATH, "aggregated_incidents.json", new_aggregated_incidents)
    print("\nUpdated aggregated incidents completed.")


if __name__ == "__main__":
    scrape()
