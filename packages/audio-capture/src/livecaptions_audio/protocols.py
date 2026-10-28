from typing import Protocol

import numpy as np


class AudioSink(Protocol):
    """Optional archival sink; the capture adapter owns no file format or paths."""

    @property
    def duration(self) -> float: ...

    def append(self, samples: np.ndarray) -> None: ...
    def samples(self, start: float, end: float) -> np.ndarray: ...
    def finish(self) -> None: ...
