"""Optional startup observations; no I/O, application or model dependencies."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from .results import Serializable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupEvent(Serializable):
    stage: str
    state: Literal["started", "completed", "failed", "reused", "cancelled"]
    seconds: float = 0


StartupObserver = Callable[[StartupEvent], None]


class StartupTimer:
    """Observe wall time. Nested stages are inclusive and must not be summed."""

    def __init__(self, observer: StartupObserver | None = None):
        self.observer = observer

    def emit(self, stage, state, seconds=0):
        event = StartupEvent(stage, state, round(seconds, 4))
        if self.observer is not None:
            try:
                self.observer(event)
            except Exception:
                logger.exception("Startup observer failed for %s", stage)

    @contextmanager
    def phase(self, stage: str) -> Iterator[None]:
        started = perf_counter()
        self.emit(stage, "started")
        try:
            yield
        except BaseException:
            self.emit(stage, "failed", perf_counter() - started)
            raise
        else:
            self.emit(stage, "completed", perf_counter() - started)
