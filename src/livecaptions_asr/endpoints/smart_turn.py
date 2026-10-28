"""Smart Turn CPU endpoint API, independent of VAD, ASR and segmentation."""

import threading
from pathlib import Path

import numpy as np

from .features import WhisperEndpointFeatures
from .runtime import cpu_session


class SmartTurnEndpoint:
    context_samples = WhisperEndpointFeatures.context_samples

    def __init__(self, session):
        self.session = session
        self.features = WhisperEndpointFeatures()
        self._lock = threading.Lock()

    @classmethod
    def from_path(cls, path: Path, *, threads: int = 1):
        return cls(cpu_session(path, threads))

    @property
    def providers(self):
        return self.session.get_providers()

    def prepare(self):
        self.probability(np.zeros(self.context_samples, dtype=np.float32))

    def probability(self, pcm):
        with self._lock:
            output = self.session.run(None, {"input_features": self.features(pcm)})[0]
        return float(output.reshape(-1)[0])
