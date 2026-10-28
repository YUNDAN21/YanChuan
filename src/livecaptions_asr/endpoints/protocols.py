"""Detection contracts independent of recognition, policy and model runtimes."""

from typing import Protocol

import numpy as np
import numpy.typing as npt


class SpeechDetector(Protocol):
    frame_samples: int

    def probability(self, pcm: npt.NDArray[np.float32]) -> float: ...
    def reset(self) -> None: ...


class EndpointDetector(Protocol):
    context_samples: int

    def probability(self, pcm: npt.NDArray[np.float32]) -> float: ...
