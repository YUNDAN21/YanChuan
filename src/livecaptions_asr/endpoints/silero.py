"""Silero CPU VAD API, independent of ASR, semantic endpoints and segmentation."""

from pathlib import Path

import numpy as np

from ..domain import ASRError, validated_pcm
from .runtime import cpu_session


class SileroSpeechDetector:
    """One stream's recurrent state; share the model session, not this object."""

    frame_samples = 512

    def __init__(self, session):
        self.session = session
        self.reset()

    def reset(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, pcm):
        pcm = validated_pcm(pcm)
        if len(pcm) != self.frame_samples:
            raise ASRError("invalid_audio", "Silero 接收每帧 512 个 16 kHz 样本。")
        audio = np.concatenate((self._context, pcm.reshape(1, -1)), axis=1)
        output, state = self.session.run(
            None, {"input": audio, "state": self._state, "sr": np.array(16000, dtype=np.int64)}
        )
        self._state = state
        self._context = audio[:, -64:].copy()
        return float(output.reshape(-1)[0])


class SileroVADModel:
    def __init__(self, session):
        self.session = session

    @classmethod
    def from_path(cls, path: Path, *, threads: int = 1):
        return cls(cpu_session(path, threads))

    @property
    def providers(self):
        return self.session.get_providers()

    def prepare(self):
        self.create_detector().probability(np.zeros(512, dtype=np.float32))

    def create_detector(self) -> SileroSpeechDetector:
        return SileroSpeechDetector(self.session)
