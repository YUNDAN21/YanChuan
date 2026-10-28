from dataclasses import dataclass

import numpy as np

from .domain import ASRError


@dataclass(frozen=True)
class Window:
    segment_id: int
    start: float
    end: float
    final: bool
    boundary: str = "partial"


class EnergySegmenter:
    """Simple RMS gate, not a learned speech detector. Times belong to captured audio."""

    def __init__(self, threshold=0.003, silence=0.6, max_seconds=8, update_seconds=1):
        if not all(
            np.isfinite(v) and v > 0 for v in (threshold, silence, max_seconds, update_seconds)
        ):
            raise ASRError("invalid_options", "分段参数必须是有限正数。")
        self.threshold = threshold
        self.silence = silence
        self.max_seconds = max_seconds
        self.update_seconds = update_seconds
        self.segment_id = 0
        self.start: float | None = None
        self.last_voice = 0.0
        self.last_update = 0.0
        self.confirmed_end = 0.0

    def feed(self, samples: np.ndarray, offset: float) -> list[Window]:
        finals = []
        for index in range(0, len(samples), 1600):
            frame = samples[index : index + 1600]
            begin = offset + index / 16000
            end = begin + len(frame) / 16000
            voiced = float(np.sqrt(np.mean(frame**2))) >= self.threshold
            if voiced:
                if self.start is None:
                    self.start = max(self.confirmed_end, begin - 0.2)
                    self.last_update = self.start
                self.last_voice = end
            if self.start is not None:
                if end - self.start >= self.max_seconds:
                    finals.append(self._finish(end, "max_duration"))
                elif end - self.last_voice >= self.silence:
                    finals.append(self._finish(min(end, self.last_voice + 0.2), "pause"))
        return finals

    def partial(self, end: float) -> Window | None:
        if self.start is None or end - self.last_update < self.update_seconds:
            return None
        self.last_update = end
        return Window(self.segment_id, self.start, end, False)

    def flush(self, end: float) -> Window | None:
        return self._finish(end, "end") if self.start is not None and end > self.start else None

    def _finish(self, end: float, boundary: str) -> Window:
        window = Window(self.segment_id, self.start, end, True, boundary)
        self.segment_id += 1
        self.confirmed_end = end
        self.start = None
        return window
