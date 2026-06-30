import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import time, datetime, timedelta

logger = logging.getLogger(__name__)


class DisruptionType(str, Enum):
    ROADBLOCK = "roadblock"
    TRAFFIC_CONGESTION = "traffic_congestion"
    TRANSIT_DISRUPTION = "transit_disruption"
    FLOODING = "flooding"
    SPECIAL_EVENT = "special_event"
    ACCIDENT = "accident"
    CONSTRUCTION = "construction"
    WEATHER = "weather"


SEVERITY = ["minor", "moderate", "severe", "critical"]

TEMPLATES = {
    DisruptionType.ROADBLOCK: {"minor": "Minor roadblock on {road} causing short delays", "moderate": "Police activity blocking one lane on {road}", "severe": "{road} closed due to emergency, all traffic diverted", "critical": "Structure failure on {road}, complete closure expected hours"},
    DisruptionType.TRAFFIC_CONGESTION: {"minor": "Heavy but flowing traffic on {road}", "moderate": "Stop-and-go traffic on {road} due to volume", "severe": "Gridlock on {road}, minimal movement", "critical": "Complete standstill on {road}, multi-hour delay"},
    DisruptionType.FLOODING: {"minor": "Ponding on {road}, drive carefully", "moderate": "Flooding on {road}, one lane closed", "severe": "{road} impassable due to standing water", "critical": "Flash flood on {road}, emergency rerouting"},
    DisruptionType.SPECIAL_EVENT: {"minor": "Small gathering near {road}", "moderate": "Marathon on {road}, rolling closures", "severe": "Major event at {venue}, surrounding streets gridlocked", "critical": "Emergency evacuation of {venue}, all roads closed"},
    DisruptionType.ACCIDENT: {"minor": "Fender-bender on {road}, shoulder blocked", "moderate": "Multi-vehicle accident on {road}, two lanes closed", "severe": "Serious accident on {road}, all lanes blocked", "critical": "Hazmat spill on {road}, extended closure"},
    DisruptionType.CONSTRUCTION: {"minor": "Utility work on {road}, single lane", "moderate": "Road resurfacing on {road}, lane reduction", "severe": "{road} reduced to one lane", "critical": "Bridge replacement on {road}, long detour"},
    DisruptionType.WEATHER: {"minor": "Light rain reducing visibility", "moderate": "Heavy rain, hydroplaning risk on {road}", "severe": "Ice on {road}, hazardous conditions", "critical": "Blizzard, {road} closed"},
}

CHICAGO_ROADS = ["I-90", "I-94", "I-290", "I-55", "I-57", "US-41", "Lake Shore Dr",
                  "Stevenson Expy", "Eisenhower Expy", "Dan Ryan Expy", "Kennedy Expy",
                  "Edens Expy", "Bishop Ford Fwy", "Wacker Dr", "Michigan Ave", "State St",
                  "Clark St", "Halsted St", "Western Ave", "Ashland Ave"]

CHICAGO_VENUES = ["United Center", "Soldier Field", "Wrigley Field",
                   "Guaranteed Rate Field", "Navy Pier", "Millennium Park",
                   "McCormick Place", "Theater District"]


@dataclass
class Disruption:
    event_id: str
    type: DisruptionType
    severity: str
    location: Tuple[float, float]
    affected_road: Optional[str] = None
    description: Optional[str] = None
    radius_meters: float = 500.0
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    affects_auto: bool = True
    affects_transit: bool = False

    def is_active_at(self, dt: datetime) -> bool:
        if self.start_datetime and dt < self.start_datetime:
            return False
        if self.end_datetime and dt > self.end_datetime:
            return False
        return True

    def to_dict(self) -> Dict:
        return {"event_id": self.event_id, "type": self.type.value, "severity": self.severity,
                "lat": self.location[0], "lon": self.location[1], "road": self.affected_road,
                "description": self.description or self._desc(),
                "radius_meters": self.radius_meters,
                "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
                "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
                "affects_auto": self.affects_auto, "affects_transit": self.affects_transit}

    def _desc(self) -> str:
        t = TEMPLATES.get(self.type, {}).get(self.severity, f"{self.type.value} near ({self.location[0]:.4f}, {self.location[1]:.4f})")
        road = self.affected_road or "a major artery"
        venue = CHICAGO_VENUES[0] if "venue" in t else ""
        return t.format(road=road, venue=venue)


class DisruptionInjector:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.active: List[Disruption] = []
        self._counter = 0

    def generate(self, type: Optional[DisruptionType] = None, severity: Optional[str] = None,
                 location: Optional[Tuple[float, float]] = None,
                 origin: Optional[Tuple[float, float]] = None,
                 destination: Optional[Tuple[float, float]] = None) -> Disruption:
        self._counter += 1
        type = type or DisruptionType.ACCIDENT
        severity = severity or "moderate"
        if location is None and origin and destination:
            location = ((origin[0] + destination[0]) / 2,
                        (origin[1] + destination[1]) / 2)
        location = location or (41.8781, -87.6298)

        e = Disruption(event_id=f"d_{self._counter}", type=type, severity=severity, location=location,
                       affected_road=CHICAGO_ROADS[0],
                       radius_meters={"minor": 200, "moderate": 500, "severe": 1000, "critical": 2000}.get(severity, 500),
                       start_datetime=datetime.now(),
                       end_datetime=datetime.now() + timedelta(minutes=60),
                       affects_auto=True,
                       affects_transit=type in (DisruptionType.TRANSIT_DISRUPTION, DisruptionType.FLOODING, DisruptionType.WEATHER))
        logger.info(f"Generated {severity} {type.value} at ({location[0]:.4f}, {location[1]:.4f}) on {e.affected_road}")
        return e

    def inject(self, origin: Tuple[float, float], destination: Tuple[float, float],
               probability: float = 0.3) -> List[Disruption]:
        events = [self.generate(origin=origin, destination=destination)]
        self.active.extend(events)
        return events

    def avoid_locations(self) -> List[Tuple[float, float]]:
        return [(e.location[0], e.location[1]) for e in self.active if e.affects_auto]

    def context(self) -> str:
        if not self.active:
            return "No active disruptions."
        return "Active disruptions:\n" + "\n".join(f"- [{e.severity.upper()}] {e.type.value}: {e._desc()}" for e in self.active)

    def reset(self):
        self.active.clear()
        self._counter = 0
