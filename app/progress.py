from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable


@dataclass
class ProgressThrottler:
    min_delta: float = 1.0
    max_interval_seconds: float = 2.0
    clock: Callable[[], float] = monotonic
    last_percent: float = 0.0
    last_update_at: float = 0.0

    def reset(self, percent: float) -> None:
        self.last_percent = float(percent)
        self.last_update_at = self.clock()

    def should_update(self, percent: float, *, force: bool = False) -> bool:
        now = self.clock()
        percent = float(percent)
        if not force:
            delta = abs(percent - self.last_percent)
            elapsed = now - self.last_update_at
            if delta < self.min_delta and elapsed < self.max_interval_seconds:
                return False

        self.last_percent = percent
        self.last_update_at = now
        return True
