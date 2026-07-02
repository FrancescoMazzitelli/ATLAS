import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple

from agentic.core.models import Location, SociodemographicProfile


def _parse_departure(departure_str: str, base_date) -> Optional[Tuple[int, int]]:
    """Parse a departure time string into (hour, minute).

    Supports formats like '8:00 AM', '14:30', '7:00 PM', '8:00', '12:30 PM (lunch!)'.
    Also handles 12-hour clock with am/pm in any position.
    Returns None if unparseable.
    """
    if not departure_str or departure_str.strip() in ("", "N/A", "TBA", "None"):
        return None
    s = departure_str.strip()
    pm = "pm" in s.lower()
    m = re.search(r'(\d{1,2})\s*:\s*(\d{2})', s)
    if not m:
        logger.debug(f"Could not parse departure time: {departure_str!r}")
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if pm and hour < 12:
        hour += 12
    if not pm and hour == 12:
        hour = 0
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        logger.debug(f"Invalid hour/minute from {departure_str!r}: ({hour}, {minute})")
        return None
    return (hour, minute)

logger = logging.getLogger(__name__)


class ActivityType:
    HOME = "home"
    WORK = "work"
    COMMUTE = "commute"
    LUNCH = "lunch"
    ERRAND = "errand"
    LEISURE = "leisure"
    OTHER = "other"


# Default durations (minutes) for each activity type
_ACTIVITY_DURATIONS = {
    ActivityType.HOME: 60,
    ActivityType.WORK: 480,
    "school": 300,
    "discretionary": 90,
    "errand": 45,
    "leisure": 120,
}


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

    @classmethod
    def from_itinerary(
        cls,
        agent_id: str,
        base_date: datetime,
        itinerary: List[Tuple[str, Location, str]],
        departure_times: List[str],
        profile: SociodemographicProfile,
        tick_duration_minutes: int = 5,
    ) -> "ActivitySchedule":
        schedule = cls(agent_id=agent_id, date=base_date.strftime("%Y-%m-%d"))
        day_start = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(hours=23, minutes=59)
        mode = "auto" if profile.has_vehicle else "transit"

        if len(itinerary) < 2:
            schedule.slots = [ActivitySlot(ActivityType.HOME, day_start, day_end, itinerary[0][1])]
            return schedule

        prev_type, prev_loc, prev_ctx = itinerary[0]
        current_time = day_start

        for i in range(1, len(itinerary)):
            curr_type, curr_loc, curr_ctx = itinerary[i]

            dep_parsed = None
            if i - 1 < len(departure_times):
                dep_parsed = _parse_departure(departure_times[i - 1], base_date)

            if dep_parsed:
                dep_hour, dep_min = dep_parsed
                depart_dt = base_date.replace(hour=dep_hour, minute=dep_min, second=0)
            else:
                depart_dt = current_time + timedelta(minutes=30)

            commute_min = _estimate_commute_minutes(prev_loc, curr_loc)
            arrive_dt = depart_dt + timedelta(minutes=commute_min)

            if prev_type == "HOME":
                prev_activity_name = ActivityType.HOME
            elif prev_type == "WORK":
                prev_activity_name = ActivityType.WORK
            elif prev_type == "SCHOOL":
                prev_activity_name = "school"
            else:
                prev_activity_name = "discretionary"

            schedule.slots.append(
                ActivitySlot(prev_activity_name, current_time, depart_dt, prev_loc, prev_ctx or "")
            )

            purpose = "commute"
            if prev_type == "HOME" and curr_type in ("WORK", "SCHOOL"):
                purpose = "commute_to_work"
            elif curr_type == "HOME":
                purpose = "commute_to_home"
            elif curr_type == "DISCRETIONARY":
                purpose = "discretionary"

            travel_slot_end = depart_dt + timedelta(minutes=max(1, commute_min // 2))
            schedule.slots.append(
                ActivitySlot(ActivityType.COMMUTE, depart_dt, arrive_dt, prev_loc, f"Traveling to {curr_type.lower()}")
            )

            dist = _haversine_km(prev_loc.lat, prev_loc.lng, curr_loc.lat, curr_loc.lng)
            schedule.trips.append(
                Trip(
                    origin=prev_loc, destination=curr_loc,
                    departure=depart_dt, arrival=arrive_dt,
                    mode=mode, purpose=purpose,
                    distance_km=dist,
                )
            )

            current_time = arrive_dt
            prev_type, prev_loc, prev_ctx = curr_type, curr_loc, curr_ctx

        last_type, last_loc, last_ctx = itinerary[-1]
        if last_type != "HOME":
            schedule.slots.append(
                ActivitySlot("discretionary" if last_type == "DISCRETIONARY" else last_type.lower(),
                             current_time, day_end, last_loc, last_ctx or "")
            )
        else:
            schedule.slots.append(
                ActivitySlot(ActivityType.HOME, current_time, day_end, last_loc, last_ctx or "")
            )

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
