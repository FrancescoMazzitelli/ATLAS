"""
Loader for user-provided disruption scenario files.

Reads JSON files containing disruption definitions and converts them
into Disruption objects for injection into the simulation.

Expected JSON format (list of objects):
  {
    "type": "roadblock",
    "severity": "severe",
    "lat": 41.8760,
    "lon": -87.6300,
    "road": "I-90",
    "description": "...",
    "radius_meters": 500,
    "start_datetime": "2019-07-17T07:30:00",
    "end_datetime": "2019-07-17T09:00:00",
    "affects_auto": true,
    "affects_transit": false
  }
"""

import json
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime

from disruption.scenarios import Disruption, DisruptionType

logger = logging.getLogger(__name__)


def load_disruptions(path: str) -> List[Disruption]:
    if not os.path.exists(path):
        logger.warning(f"Disruption file not found: {path}")
        return []

    with open(path) as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        logger.error(f"Expected a JSON array in {path}, got {type(raw).__name__}")
        return []

    disruptions: List[Disruption] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            logger.warning(f"Skipping item {i}: not a dict")
            continue

        try:
            d_type = DisruptionType(item.get("type", ""))
        except ValueError:
            valid = [e.value for e in DisruptionType]
            logger.warning(f"Skipping item {i}: invalid type {item.get('type')!r} (valid: {valid})")
            continue

        severity = item.get("severity", "moderate")
        if severity not in ("minor", "moderate", "severe", "critical"):
            logger.warning(f"Item {i}: unknown severity {severity!r}, defaulting to moderate")
            severity = "moderate"

        lat = item.get("lat")
        lon = item.get("lon")
        if lat is None or lon is None:
            logger.warning(f"Skipping item {i}: missing lat/lon")
            continue

        start_dt = _parse_dt(item.get("start_datetime"))
        end_dt = _parse_dt(item.get("end_datetime"))

        d = Disruption(
            event_id=item.get("id", f"file_{i+1:03d}"),
            type=d_type,
            severity=severity,
            location=(float(lat), float(lon)),
            affected_road=item.get("road"),
            description=item.get("description"),
            radius_meters=float(item.get("radius_meters", 500)),
            start_datetime=start_dt,
            end_datetime=end_dt,
            affects_auto=bool(item.get("affects_auto", True)),
            affects_transit=bool(item.get("affects_transit", False)),
        )
        disruptions.append(d)

    logger.info(f"Loaded {len(disruptions)} disruptions from {path}")
    return disruptions


def load_disruptions_from_paths(paths: List[str]) -> List[Disruption]:
    all_d: List[Disruption] = []
    for p in paths:
        all_d.extend(load_disruptions(p))
    return all_d


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        logger.warning(f"Invalid datetime: {s!r}")
        return None
