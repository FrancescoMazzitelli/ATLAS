"""
Loader for user-provided disruption scenario files.

Reads JSON/JSONL files containing disruption definitions and converts them
into Disruption objects for injection into the simulation.

Supported formats:

JSON (list of objects):
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

JSONL (one JSON object per line — crash/disruption data):
  {
    "date": "2020-07-27",
    "seconds": 34200,
    "duration_seconds": 1800,
    "end_seconds": 36000,
    "x": -87.6208,
    "y": 41.7975,
    "weather": "RAIN",
    "injuries_total": 0
  }
  Severity is derived from injuries_total:
    - 0  → minor
    - 1  → moderate
    - 2  → severe
    - 3+ → critical

Both formats are auto-detected regardless of file extension (.json or .jsonl).
"""

import json
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from itertools import islice

from disruption.scenarios import Disruption, DisruptionType

logger = logging.getLogger(__name__)


def _detect_format_and_load(path: str) -> List[dict]:
    """Detect whether a file is JSON array or JSONL and return parsed items.

    Strategy:
    1. Read the first non-empty line.
       - If it starts with '[' → treat as JSON array.
       - Otherwise → treat as JSONL (one object per line).
    2. Falls back to JSONL if JSON array parsing fails.
    """
    if not os.path.exists(path):
        logger.warning(f"Disruption file not found: {path}")
        return []

    with open(path) as f:
        first_line = None
        for line in f:
            stripped = line.strip()
            if stripped:
                first_line = stripped
                break

        if first_line is None:
            logger.warning(f"Empty disruption file: {path}")
            return []

        if first_line.startswith("["):
            f.seek(0)
            try:
                raw = json.load(f)
                if isinstance(raw, list):
                    return raw
                logger.warning(f"Expected JSON array in {path}, got {type(raw).__name__}")
                return []
            except json.JSONDecodeError as e:
                logger.warning(f"JSON array parse failed for {path}, trying JSONL: {e}")

        f.seek(0)
        items = []
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    items.append(item)
                else:
                    logger.warning(f"Skipping line {i}: not a dict")
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping line {i}: invalid JSON - {e}")

    return items


def load_disruptions(path: str) -> List[Disruption]:
    items = _detect_format_and_load(path)
    if not items:
        return []

    disruptions: List[Disruption] = []
    for i, item in enumerate(items):
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


def _parse_jsonl_item(item: dict, index: int) -> Optional[Disruption]:
    """Parse a JSONL crash/accident record into a Disruption."""
    try:
        lat = item.get("y")
        lon = item.get("x")
        if lat is None or lon is None:
            logger.warning(f"Skipping JSONL item {index}: missing x/y")
            return None

        injuries = int(item.get("injuries_total", 0))
        if injuries >= 3:
            severity = "critical"
        elif injuries >= 2:
            severity = "severe"
        elif injuries >= 1:
            severity = "moderate"
        else:
            severity = "minor"

        date_str = item.get("date", "")
        seconds = int(item.get("seconds", 0))
        duration = int(item.get("duration_seconds", 1800))

        start_dt = None
        end_dt = None
        if date_str:
            try:
                start_dt = datetime.fromisoformat(date_str)
                start_dt = start_dt.replace(
                    hour=seconds // 3600,
                    minute=(seconds % 3600) // 60,
                    second=seconds % 60,
                )
                end_dt = start_dt + timedelta(seconds=duration)
            except (ValueError, TypeError):
                logger.warning(f"JSONL item {index}: invalid date {date_str!r}")

        weather = item.get("weather", "CLEAR")
        cause = item.get("cause", "")
        if weather in ("RAIN", "SNOW", "CLOUDY/OVERCAST"):
            d_type = DisruptionType.WEATHER
        else:
            d_type = DisruptionType.ACCIDENT

        description = cause or item.get("description")
        if not description:
            if d_type == DisruptionType.WEATHER:
                description = f"{weather} weather conditions"
            else:
                description = f"Accident at ({float(lat):.4f}, {float(lon):.4f})"

        return Disruption(
            event_id=item.get("id", f"jsonl_{index+1:04d}"),
            type=d_type,
            severity=severity,
            location=(float(lat), float(lon)),
            affected_road=None,
            description=description,
            radius_meters=500.0,
            start_datetime=start_dt,
            end_datetime=end_dt,
            affects_auto=True,
            affects_transit=False,
            cause=cause,
            weather=weather,
        )
    except Exception as e:
        logger.warning(f"Error processing JSONL item {index}: {e}")
        return None


def _is_jsonl_format(item: dict) -> bool:
    """Detect if a dict is in JSONL crash format (has x/y/date keys)."""
    return "x" in item and "y" in item and "seconds" in item


def load_disruptions(path: str) -> List[Disruption]:
    items = _detect_format_and_load(path)
    if not items:
        return []

    disruptions: List[Disruption] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            logger.warning(f"Skipping item {i}: not a dict")
            continue

        # JSONL crash format (x, y, date, seconds, injuries_total, weather)
        if _is_jsonl_format(item):
            d = _parse_jsonl_item(item, i)
            if d:
                disruptions.append(d)
            continue

        # Standard JSON format (type, lat, lon, severity, ...)
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
