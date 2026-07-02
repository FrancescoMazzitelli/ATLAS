import os
import yaml
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ClockConfig:
    start_datetime: str = "2019-07-17T07:00:00"
    tick_duration_minutes: int = 5
    tick_duration_seconds: int = 1
    max_ticks: int = 288


@dataclass
class ValhallaConfig:
    host: str = "localhost"
    port: int = 8002
    timeout: int = 30


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "llama3.2"
    temperature: float = 0.5
    base_url: str = "http://localhost:11434"
    num_predict: int = 4096


@dataclass
class SimulationConfig:
    seed: int = 42
    log_level: str = "INFO"
    recursion_limit: int = 50
    disruption_files: List[str] = field(default_factory=list)
    clock: ClockConfig = field(default_factory=ClockConfig)


@dataclass
class DiscretionaryConfig:
    enabled: bool = True
    social_invitation: str = "A colleague from work invites you to grab a beer after work. You had planned to {context}. What do you do?"
    accept_probability: float = 0.5


@dataclass
class PathConfig:
    step_km: float = 1.0


@dataclass
class NominatimConfig:
    host: str = ""
    port: int = 8080
    timeout: int = 10


@dataclass
class DataConfig:
    agents_file: str = "output.jsonl"
    locations: str = ""
    num_agents: int = 50


@dataclass
class AgentProfileConfig:
    age: int
    sex: str
    race: Optional[str] = None
    income: Optional[float] = None
    occupation: Optional[str] = None
    has_vehicle: bool = True
    has_transit_pass: bool = False
    risk_tolerance: float = 0.5
    mobility_constraints: list = field(default_factory=list)


@dataclass
class LocationConfig:
    name: str
    lat: float
    lng: float


@dataclass
class AgentConfig:
    id: str
    profile: AgentProfileConfig
    home: LocationConfig
    work: Optional[LocationConfig] = None


@dataclass
class TrafficConfig:
    csv_dir: str = "traffic"
    jam_density_per_km: float = 50.0
    docker_container: str = ""
    valhalla_config: str = "/etc/valhalla/valhalla.json"
    container_traffic_dir: str = "/traffic"
    traffic_backup_dir: str = "traffic_backup"


@dataclass
class OutputConfig:
    dir: str = "output"
    generate_map: bool = True
    map_zoom: int = 12
    center_lat: float = 41.8781
    center_lon: float = -87.6298


@dataclass
class Config:
    valhalla: ValhallaConfig = field(default_factory=ValhallaConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    nominatim: NominatimConfig = field(default_factory=NominatimConfig)
    data: DataConfig = field(default_factory=DataConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    discretionary: DiscretionaryConfig = field(default_factory=DiscretionaryConfig)
    path: PathConfig = field(default_factory=PathConfig)
    traffic: TrafficConfig = field(default_factory=TrafficConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    agents: list = field(default_factory=list)


def _dict_to_dataclass(d: dict, cls):
    try:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
    except TypeError:
        return cls()


def load_config(path: str = "config.yaml") -> Config:
    if not os.path.isabs(path):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo_root, path)
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    cfg = Config()
    cfg.valhalla = _dict_to_dataclass(raw.get("valhalla", {}), ValhallaConfig)
    cfg.llm = _dict_to_dataclass(raw.get("llm", {}), LLMConfig)
    cfg.nominatim = _dict_to_dataclass(raw.get("nominatim", {}), NominatimConfig)
    cfg.data = _dict_to_dataclass(raw.get("data", {}), DataConfig)
    raw_sim = raw.get("simulation", {})
    if isinstance(raw_sim.get("clock"), dict):
        raw_sim["clock"] = ClockConfig(**raw_sim["clock"])
    cfg.simulation = _dict_to_dataclass(raw_sim, SimulationConfig)
    cfg.discretionary = _dict_to_dataclass(raw.get("discretionary", {}), DiscretionaryConfig)
    cfg.path = _dict_to_dataclass(raw.get("path", {}), PathConfig)
    cfg.traffic = _dict_to_dataclass(raw.get("traffic", {}), TrafficConfig)
    cfg.output = _dict_to_dataclass(raw.get("output", {}), OutputConfig)
    raw_agents = raw.get("agents", [])
    cfg.agents = []
    for a in raw_agents:
        try:
            cfg.agents.append(AgentConfig(
                id=a["id"],
                profile=AgentProfileConfig(**a["profile"]),
                home=LocationConfig(**a["home"]),
                work=LocationConfig(**a["work"]) if a.get("work") else None,
            ))
        except (KeyError, TypeError) as e:
            logger.warning(f"Skipping agent config: {e}")
    return cfg
