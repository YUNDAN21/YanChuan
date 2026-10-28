"""Public value objects. No transport, device, storage or model dependencies."""

from dataclasses import asdict, dataclass
from typing import Literal


def _json_value(value):
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


class Serializable:
    def to_dict(self) -> dict:
        """Explicit serialization boundary for CLI, HTTP and storage adapters."""
        return _json_value(asdict(self))


@dataclass(frozen=True)
class Timing(Serializable):
    decode_seconds: float = 0
    queue_seconds: float = 0
    load_seconds: float = 0
    inference_seconds: float = 0
    total_seconds: float = 0
    rtf: float = 0


@dataclass(frozen=True)
class RecognitionResult(Serializable):
    text: str
    language: str
    language_mode: str
    duration_seconds: float
    silent: bool
    model: str
    backend: str
    device: str | None
    dtype: str | None
    timing: Timing
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Segment(Serializable):
    id: int
    start_seconds: float
    end_seconds: float
    text: str
    language: str = "unknown"
    final: bool = True
    boundary: str = "end"
    revision: int = 0
    inference_seconds: float = 0


@dataclass(frozen=True)
class Sentence(Serializable):
    id: int
    start_seconds: float
    end_seconds: float
    text: str
    segment_ids: tuple[int, ...]
    boundary: str


@dataclass(frozen=True)
class TranscriptionResult(RecognitionResult):
    segments: tuple[Segment, ...] = ()
    semantic_sections: tuple[Sentence, ...] = ()
    chunks_completed: int = 0


@dataclass(frozen=True)
class FileProgress(Serializable):
    processed_seconds: float
    chunks_completed: int
    segment: Segment
    semantic_sections: tuple[Sentence, ...] = ()


@dataclass(frozen=True)
class StreamEvent(Serializable):
    kind: Literal["partial", "final", "sentence"]
    segment: Segment | None = None
    sentence: Sentence | None = None
