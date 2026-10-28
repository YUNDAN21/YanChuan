"""Composable ASR/detection APIs; exports load only the requested component."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audio import AudioChunk, AudioDecoder
    from .domain import (
        ASRError,
        AudioData,
        EngineStatus,
        Recognition,
        RecognitionEngine,
        ReleasableEngine,
        WarmableEngine,
    )
    from .endpoints.protocols import EndpointDetector, SpeechDetector
    from .endpoints.silero import SileroVADModel
    from .endpoints.smart_turn import SmartTurnEndpoint
    from .model_segmentation import EndpointOptions, ModelSegmenter
    from .recognizer import Cancellation, FileDecoder, Recognizer, RecognizerOptions
    from .results import (
        FileProgress,
        RecognitionResult,
        Segment,
        Sentence,
        StreamEvent,
        Timing,
        TranscriptionResult,
    )
    from .segmentation import EnergySegmenter, Window
    from .semantic import SemanticGrouper
    from .startup import StartupEvent, StartupObserver, StartupTimer
    from .stream import PCMStream, SentenceGrouper, StreamOptions, WindowRecognizer, WindowSegmenter
    from .subtitles import SubtitleCue, SubtitleSplitter

__all__ = [
    "ASRError",
    "AudioChunk",
    "AudioData",
    "AudioDecoder",
    "Cancellation",
    "EndpointDetector",
    "EndpointOptions",
    "EnergySegmenter",
    "EngineStatus",
    "FileDecoder",
    "FileProgress",
    "ModelSegmenter",
    "PCMStream",
    "Recognition",
    "RecognitionEngine",
    "RecognitionResult",
    "Recognizer",
    "RecognizerOptions",
    "ReleasableEngine",
    "Segment",
    "SemanticGrouper",
    "Sentence",
    "SentenceGrouper",
    "SileroVADModel",
    "SmartTurnEndpoint",
    "SpeechDetector",
    "StartupEvent",
    "StartupObserver",
    "StartupTimer",
    "StreamEvent",
    "StreamOptions",
    "SubtitleCue",
    "SubtitleSplitter",
    "Timing",
    "TranscriptionResult",
    "WarmableEngine",
    "Window",
    "WindowRecognizer",
    "WindowSegmenter",
]

_EXPORT_MODULES = {
    "ASRError": "domain",
    "AudioChunk": "audio",
    "AudioData": "domain",
    "AudioDecoder": "audio",
    "Cancellation": "recognizer",
    "EndpointDetector": "endpoints.protocols",
    "EndpointOptions": "model_segmentation",
    "EnergySegmenter": "segmentation",
    "EngineStatus": "domain",
    "FileDecoder": "recognizer",
    "FileProgress": "results",
    "ModelSegmenter": "model_segmentation",
    "PCMStream": "stream",
    "Recognition": "domain",
    "RecognitionEngine": "domain",
    "ReleasableEngine": "domain",
    "WarmableEngine": "domain",
    "RecognitionResult": "results",
    "Recognizer": "recognizer",
    "RecognizerOptions": "recognizer",
    "Segment": "results",
    "SemanticGrouper": "semantic",
    "Sentence": "results",
    "SentenceGrouper": "stream",
    "SileroVADModel": "endpoints.silero",
    "SmartTurnEndpoint": "endpoints.smart_turn",
    "SpeechDetector": "endpoints.protocols",
    "StartupEvent": "startup",
    "StartupObserver": "startup",
    "StartupTimer": "startup",
    "SubtitleCue": "subtitles",
    "SubtitleSplitter": "subtitles",
    "StreamEvent": "results",
    "StreamOptions": "stream",
    "Timing": "results",
    "TranscriptionResult": "results",
    "Window": "segmentation",
    "WindowRecognizer": "stream",
    "WindowSegmenter": "stream",
}


def __getattr__(name: str):
    try:
        module = _EXPORT_MODULES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
