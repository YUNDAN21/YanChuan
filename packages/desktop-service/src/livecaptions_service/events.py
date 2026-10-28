"""In-process observations; serialization belongs to the process adapter."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceEvent:
    sequence: int
    session_id: str | None
    name: str
    payload: Any


EventObserver = Callable[[ServiceEvent], None]
