from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import time


@dataclass
class Location:
    location_id: str
    name: str
    lat: float
    lng: float
    location_type: str
    address: Optional[str] = None


@dataclass
class SociodemographicProfile:
    age: int
    sex: str
    race: Optional[str] = None
    ancestry: Optional[str] = None
    income: Optional[float] = None
    education_level: Optional[str] = None
    occupation: Optional[str] = None
    work_hours_per_week: Optional[float] = None
    has_vehicle: bool = True
    has_transit_pass: bool = False
    risk_tolerance: float = 0.5
    mobility_constraints: List[str] = field(default_factory=list)
    home_location: Optional[Location] = None
    work_location: Optional[Location] = None

    def to_dict(self) -> Dict:
        return {"age": self.age, "sex": self.sex, "race": self.race,
                "ancestry": self.ancestry, "income": self.income,
                "occupation": self.occupation, "education": self.education_level,
                "has_vehicle": self.has_vehicle, "has_transit_pass": self.has_transit_pass,
                "risk_tolerance": self.risk_tolerance,
                "mobility_constraints": self.mobility_constraints}

    def to_text(self) -> str:
        parts = [f"Age: {self.age}", f"Sex: {self.sex}"]
        if self.race: parts.append(f"Race: {self.race}")
        if self.occupation: parts.append(f"Occupation: {self.occupation}")
        if self.income: parts.append(f"Income: ${self.income:,.0f}")
        parts.append(f"Vehicle: {self.has_vehicle}")
        parts.append(f"Transit pass: {self.has_transit_pass}")
        parts.append(f"Risk tolerance: {self.risk_tolerance:.2f}")
        if self.mobility_constraints:
            parts.append(f"Mobility: {', '.join(self.mobility_constraints)}")
        if self.home_location:
            parts.append(f"Home: {self.home_location.name}")
        if self.work_location:
            parts.append(f"Work: {self.work_location.name}")
        return "\n".join(parts)


@dataclass
class ActivityPreference:
    activity_type: str
    preferred_time: Optional[time] = None
    frequency: str = "daily"
    typical_duration_minutes: int = 60
    location_id: Optional[str] = None
