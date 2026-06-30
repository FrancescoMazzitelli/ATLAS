import numpy as np
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import time, datetime, timedelta

from agentic.core.models import ActivityPreference, Location, SociodemographicProfile

logger = logging.getLogger(__name__)


@dataclass
class Trip:
    origin: Location
    destination: Location
    departure_time: time
    purpose: str
    mode: str = "driving"
    duration_minutes: float = 30.0
    distance_km: float = 10.0

    def to_dict(self) -> Dict:
        return {"origin": {"lat": self.origin.lat, "lng": self.origin.lng, "name": self.origin.name},
                "destination": {"lat": self.destination.lat, "lng": self.destination.lng, "name": self.destination.name},
                "departure_time": self.departure_time.strftime("%H:%M"), "purpose": self.purpose,
                "mode": self.mode, "duration_minutes": self.duration_minutes, "distance_km": self.distance_km}


@dataclass
class DailySchedule:
    agent_id: str
    date: str
    activities: List[Dict] = field(default_factory=list)
    trips: List[Trip] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {"agent_id": self.agent_id, "date": self.date,
                "activities": self.activities, "trips": [t.to_dict() for t in self.trips]}


class RoutineGenerator:
    def __init__(self, profile: SociodemographicProfile):
        self.profile = profile

    def generate(self, date_str: Optional[str] = None) -> DailySchedule:
        schedule = DailySchedule(agent_id="", date=date_str or datetime.now().strftime("%Y-%m-%d"))
        home, work = self.profile.home_location, self.profile.work_location

        if home is None:
            logger.warning("No home location")
            return schedule

        has_work = work is not None and self.profile.occupation is not None

        if has_work:
            commute = self._estimate_commute(home, work)
            w_start, w_end = time(9, 0), time(17, 0)
            depart = self._add(-int(commute), w_start)

            schedule.activities.extend([
                {"type": "home", "start_time": "00:00", "end_time": depart.strftime("%H:%M"), "location_id": home.location_id, "location_name": home.name},
                {"type": "work", "start_time": w_start.strftime("%H:%M"), "end_time": w_end.strftime("%H:%M"), "location_id": work.location_id, "location_name": work.name},
                {"type": "lunch_break", "start_time": "12:00", "end_time": "13:00", "location_id": work.location_id, "location_name": work.name},
                {"type": "home", "start_time": self._add(int(commute), w_end).strftime("%H:%M"), "end_time": "23:59", "location_id": home.location_id, "location_name": home.name},
            ])
            schedule.trips.extend([
                Trip(origin=home, destination=work, departure_time=depart, purpose="commute_to_work",
                     mode="driving" if self.profile.has_vehicle else "transit",
                     duration_minutes=commute, distance_km=self._dist(home, work)),
                Trip(origin=work, destination=home, departure_time=w_end, purpose="commute_to_home",
                     mode="driving" if self.profile.has_vehicle else "transit",
                     duration_minutes=commute, distance_km=self._dist(work, home)),
            ])
        else:
            schedule.activities.append({"type": "home", "start_time": "00:00", "end_time": "23:59",
                                        "location_id": home.location_id, "location_name": home.name})

        return schedule

    def _estimate_commute(self, o: Location, d: Location) -> float:
        return max(15.0, min(90.0, self._dist(o, d) / 40 * 60))

    @staticmethod
    def _dist(o: Location, d: Location) -> float:
        return np.sqrt((o.lat - d.lat) ** 2 + (o.lng - d.lng) ** 2) * 111.0

    @staticmethod
    def _add(m: int, t: time) -> time:
        return (datetime(2000, 1, 1, t.hour, t.minute) + timedelta(minutes=m)).time()
