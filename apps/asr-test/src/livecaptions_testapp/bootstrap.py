"""Application composition only; the public library never imports this module."""

from dataclasses import fields

from livecaptions_asr.audio import AudioDecoder
from livecaptions_asr.domain import RecognitionEngine
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine
from livecaptions_asr.recognizer import Recognizer, RecognizerOptions

from .config import Settings


def build_recognizer(config: Settings, engine: RecognitionEngine | None = None) -> Recognizer:
    options = NativeQwenOptions(
        **{field.name: getattr(config, field.name) for field in fields(NativeQwenOptions)}
    )
    return Recognizer(
        engine if engine is not None else NativeQwenEngine(options),
        AudioDecoder(config.max_upload_bytes, config.max_audio_seconds),
        RecognizerOptions(
            config.max_inflight_requests, config.file_chunk_seconds, config.semantic_max_seconds
        ),
    )
