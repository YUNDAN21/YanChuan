from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt


class ASRError(Exception):
    """Transport-independent error; API and CLI decide how to present it."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


LANGUAGES = {"zh": "Chinese", "ja": "Japanese", "en": "English"}


def validated_pcm(samples: npt.ArrayLike, *, allow_empty: bool = False) -> npt.NDArray[np.float32]:
    try:
        if np.iscomplexobj(samples):
            raise ValueError("complex PCM")
        pcm = np.ascontiguousarray(samples, dtype=np.float32)
        if pcm.ndim != 1 or (not allow_empty and not pcm.size) or not np.isfinite(pcm).all():
            raise ValueError("invalid PCM")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ASRError("invalid_audio", "输入必须是有效的 16 kHz 单声道浮点 PCM。") from exc
    return pcm


def normalize_language(value: str | None) -> str | None:
    if value is None or value.strip().lower() in {"", "auto"}:
        return None
    language = value.strip().lower()
    if language not in LANGUAGES:
        raise ASRError("unsupported_language", "language 必须是 auto、zh、ja 或 en。")
    return LANGUAGES[language]


@dataclass(frozen=True)
class AudioData:
    samples: npt.NDArray[np.float32]
    sample_rate: int
    duration_seconds: float
    is_silent: bool

    def __post_init__(self):
        pcm = validated_pcm(self.samples)
        try:
            valid_duration = np.isfinite(self.duration_seconds) and self.duration_seconds > 0
            valid_duration = (
                valid_duration and abs(self.duration_seconds - len(pcm) / 16000) <= 1 / 16000 + 1e-9
            )
        except TypeError:
            valid_duration = False
        if self.sample_rate != 16000 or not valid_duration:
            raise ASRError("invalid_audio", "音频必须为 16 kHz，时长须与采样帧数一致。")
        if self.is_silent != bool(np.max(np.abs(pcm)) <= 1e-7):
            raise ASRError("invalid_audio", "静音标记与音频内容不一致。")
        object.__setattr__(self, "samples", pcm)

    @classmethod
    def from_pcm16k(cls, samples: npt.ArrayLike, duration: float | None = None):
        samples = validated_pcm(samples)
        return cls(
            samples,
            16000,
            len(samples) / 16000 if duration is None else duration,
            bool(np.max(np.abs(samples)) <= 1e-7),
        )


@dataclass(frozen=True)
class Recognition:
    text: str
    language: str


@dataclass(frozen=True)
class EngineStatus:
    model_id: str
    backend: str = "qwen3-ggml-vulkan"
    state: Literal["unloaded", "loading", "ready", "error"] = "unloaded"
    device: str | None = None
    dtype: str | None = None
    load_count: int = 0
    load_seconds: float = 0
    last_error: str | None = None
    supports_streaming: bool = False
    runtime_version: str | None = None
    device_name: str | None = None


class RecognitionEngine(Protocol):
    """Replace this adapter to change the inference runtime, without editing API code."""

    def status(self) -> EngineStatus: ...

    def load(self) -> None: ...

    def recognize(self, audio: AudioData, language: str | None, context: str) -> Recognition: ...


class ReleasableEngine(RecognitionEngine, Protocol):
    """Optional lifecycle extension for adapters that own persistent model resources."""

    def close(self) -> None: ...


class WarmableEngine(RecognitionEngine, Protocol):
    """Optional bounded warmup; the caller discards synthetic inference output."""

    def warmup(self) -> None: ...
