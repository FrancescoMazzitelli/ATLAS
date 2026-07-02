import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class SimulationClock:
    def __init__(self, start_datetime: datetime,
                 tick_duration_seconds: int = 1,
                 max_ticks: int = 86400):
        self.start_datetime = start_datetime
        self.tick_duration = timedelta(seconds=tick_duration_seconds)
        self.tick_duration_seconds = tick_duration_seconds
        self.max_ticks = max_ticks
        self._current_tick = 0

    @property
    def current_tick(self) -> int:
        return self._current_tick

    def current_datetime(self) -> datetime:
        return self.start_datetime + self.tick_duration * self._current_tick

    def advance(self) -> bool:
        if self._current_tick >= self.max_ticks:
            return False
        self._current_tick += 1
        return True

    def reset(self):
        self._current_tick = 0

    def is_done(self) -> bool:
        return self._current_tick >= self.max_ticks

    def fraction_complete(self) -> float:
        return self._current_tick / self.max_ticks if self.max_ticks > 0 else 0.0

    def seconds_since_start(self) -> int:
        return self._current_tick * self.tick_duration_seconds
