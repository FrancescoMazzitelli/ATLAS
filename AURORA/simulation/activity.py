import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple

from agentic.core.models import Location, SociodemographicProfile

logger = logging.getLogger(__name__)


class ActivityType:
    HOME = "home"
    WORK = "work"
    COMMUTE = "commute"
    LUNCH = "lunch"
    ERRAND = "errand"
    LEISURE = "leisure"
    OTHER = "other"


@dataclass
class ActivitySlot:
    activity_type: str
    start: datetime
    end: datetime
    location: Location
    description: str = ""

    def contains(self, dt: datetime) -> bool:
        return self.start <= dt < self.end

    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def to_dict(self) -> Dict:
        return {
            "type": self.activity_type,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "location": {"name": self.location.name, "lat": self.location.lat, "lon": self.location.lng},
            "description": self.description,
        }


@dataclass
class Trip:
    origin: Location
    destination: Location
    departure: datetime
    arrival: datetime
    mode: str
    purpose: str = "commute"
    distance_km: float = 0.0

    def contains(self, dt: datetime) -> bool:
        return self.departure <= dt < self.arrival

    def duration_minutes(self) -> float:
        return (self.arrival - self.departure).total_seconds() / 60

    def progress_at(self, dt: datetime) -> float:
        if dt <= self.departure:
            return 0.0
        if dt >= self.arrival:
            return 1.0
        elapsed = (dt - self.departure).total_seconds()
        total = (self.arrival - self.departure).total_seconds()
        return elapsed / total if total > 0 else 0.0

    def to_dict(self) -> Dict:
        return {
            "origin": {"lat": self.origin.lat, "lon": self.origin.lng, "name": self.origin.name},
            "destination": {"lat": self.destination.lat, "lon": self.destination.lng, "name": self.destination.name},
            "departure": self.departure.isoformat(),
            "arrival": self.arrival.isoformat(),
            "mode": self.mode,
            "purpose": self.purpose,
            "distance_km": round(self.distance_km, 2),
        }


@dataclass
class ActivitySchedule:
    agent_id: str
    date: str
    slots: List[ActivitySlot] = field(default_factory=list)
    trips: List[Trip] = field(default_factory=list)

    def current_activity(self, dt: datetime) -> Optional[ActivitySlot]:
        for slot in self.slots:
            if slot.contains(dt):
                return slot
        return None

    def current_trip(self, dt: datetime) -> Optional[Trip]:
        for trip in self.trips:
            if trip.contains(dt):
                return trip
        return None

    def status_at(self, dt: datetime) -> Dict:
        trip = self.current_trip(dt)
        if trip:
            return {"type": "traveling", "purpose": trip.purpose, "mode": trip.mode,
                    "origin": {"lat": trip.origin.lat, "lon": trip.origin.lng},
                    "destination": {"lat": trip.destination.lat, "lon": trip.destination.lng},
                    "progress": trip.progress_at(dt)}
        activity = self.current_activity(dt)
        if activity:
            return {"type": "activity", "activity_type": activity.activity_type,
                    "location": {"lat": activity.location.lat, "lon": activity.location.lng},
                    "location_name": activity.location.name}
        return {"type": "idle", "activity_type": "unknown"}

    def to_dict(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "date": self.date,
            "slots": [s.to_dict() for s in self.slots],
            "trips": [t.to_dict() for t in self.trips],
        }

    @classmethod
    def from_profile(cls, agent_id: str, base_date: datetime,
                     profile: SociodemographicProfile,
                     tick_duration_minutes: int = 5) -> "ActivitySchedule":
        home = profile.home_location
        work = profile.work_location
        has_work = work is not None and profile.occupation is not None
        schedule = cls(agent_id=agent_id, date=base_date.strftime("%Y-%m-%d"))

        day_start = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(hours=23, minutes=59)

        if has_work:
            commute_min = _estimate_commute_minutes(home, work)
            work_start = base_date.replace(hour=9, minute=0)
            work_end = base_date.replace(hour=17, minute=0)
            depart_home = work_start - timedelta(minutes=commute_min)
            lunch_start = base_date.replace(hour=12, minute=0)
            lunch_end = base_date.replace(hour=13, minute=0)
            return_home = work_end + timedelta(minutes=commute_min)

            mode = "auto" if profile.has_vehicle else "transit"

            schedule.slots = [
                ActivitySlot(ActivityType.HOME, day_start, depart_home, home),
                ActivitySlot(ActivityType.COMMUTE, depart_home, work_start, home, "Commuting to work"),
                ActivitySlot(ActivityType.WORK, work_start, lunch_start, work),
                ActivitySlot(ActivityType.LUNCH, lunch_start, lunch_end, work, "Lunch break"),
                ActivitySlot(ActivityType.WORK, lunch_end, work_end, work),
                ActivitySlot(ActivityType.COMMUTE, work_end, return_home, work, "Commuting home"),
                ActivitySlot(ActivityType.HOME, return_home, day_end, home),
            ]

            home_to_work_dist = _haversine_km(home.lat, home.lng, work.lat, work.lng)
            schedule.trips = [
                Trip(origin=home, destination=work,
                     departure=depart_home, arrival=work_start,
                     mode=mode, purpose="commute_to_work",
                     distance_km=home_to_work_dist),
                Trip(origin=work, destination=home,
                     departure=work_end, arrival=return_home,
                     mode=mode, purpose="commute_to_home",
                     distance_km=home_to_work_dist),
            ]
        else:
            schedule.slots = [
                ActivitySlot(ActivityType.HOME, day_start, day_end, home),
            ]

        return schedule


def _estimate_commute_minutes(home: Location, work: Location) -> int:
    dist = _haversine_km(home.lat, home.lng, work.lat, work.lng)
    speed_kmh = 40.0
    minutes = (dist / speed_kmh) * 60
    return max(10, min(90, int(minutes)))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
