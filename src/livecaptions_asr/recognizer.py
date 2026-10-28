import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from time import perf_counter
from typing import TYPE_CHECKING, Protocol

import numpy as np

from .audio import AudioChunk, AudioDecoder, AudioSource
from .domain import (
    ASRError,
    AudioData,
    EngineStatus,
    Recognition,
    RecognitionEngine,
    normalize_language,
)
from .results import FileProgress, RecognitionResult, Segment, Timing, TranscriptionResult
from .semantic import SemanticGrouper
from .startup import StartupObserver, StartupTimer

if TYPE_CHECKING:
    from .stream import PCMStream, SentenceGrouper, StreamOptions, WindowSegmenter


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


class FileDecoder(Protocol):
    def chunks(
        self, source: AudioSource, chunk_seconds: float = 20
    ) -> Generator[AudioChunk, None, None]: ...


@dataclass(frozen=True)
class RecognizerOptions:
    max_inflight: int = 2
    chunk_seconds: float = 20
    semantic_seconds: float = 60

    def __post_init__(self):
        if (
            not isinstance(self.max_inflight, int)
            or isinstance(self.max_inflight, bool)
            or self.max_inflight < 1
            or not all(
                np.isfinite(value) and value > 0
                for value in (self.chunk_seconds, self.semantic_seconds)
            )
        ):
            raise ASRError("invalid_options", "识别容量和分段时长必须为正数。")
        if self.chunk_seconds < 0.1:
            raise ASRError("invalid_options", "文件识别窗口至少需要 0.1 秒。")


def empty_timing():
    return {
        "decode_seconds": 0.0,
        "queue_seconds": 0.0,
        "load_seconds": 0.0,
        "inference_seconds": 0.0,
    }


class Recognizer:
    """One inference path shared by live windows and sequential file chunks."""

    def __init__(
        self,
        engine: RecognitionEngine,
        decoder: FileDecoder | None = None,
        options: RecognizerOptions | None = None,
    ):
        self.engine, self.decoder = engine, decoder if decoder is not None else AudioDecoder()
        self.options = options or RecognizerOptions()
        self._capacity = threading.BoundedSemaphore(self.options.max_inflight)
        self._inference_lock = threading.Lock()
        self._warmed = False

    @contextmanager
    def _admitted(self):
        if not self._capacity.acquire(blocking=False):
            raise ASRError("server_busy", "识别队列已满，请稍后重试。")
        try:
            yield
        finally:
            self._capacity.release()

    def prepare(self, *, on_progress: StartupObserver | None = None):
        """Warm once before opening live input; discard the synthetic result."""
        timer = StartupTimer(on_progress)
        with timer.phase("recognizer_prepare"), self._admitted():
            with timer.phase("recognizer_queue"):
                self._inference_lock.acquire()
            try:
                with timer.phase("engine_load"):
                    self.engine.load()
                if not self._warmed:
                    with timer.phase("warmup"):
                        warmup = getattr(self.engine, "warmup", None)
                        if warmup is not None:
                            warmup()
                        else:
                            self.engine.recognize(AudioData.from_pcm16k(np.zeros(16000)), None, "")
                        self._warmed = True
                else:
                    timer.emit("warmup", "reused")
            finally:
                self._inference_lock.release()

    def _infer(self, audio: AudioData, language, context, cancel=None):
        timing = empty_timing()
        if audio.is_silent:
            return Recognition("", "unknown"), timing
        queued = perf_counter()
        with self._inference_lock:
            timing["queue_seconds"] = perf_counter() - queued
            if cancel is not None and cancel.is_set():
                raise ASRError("cancelled", "已取消识别。")
            loading = perf_counter()
            was_ready = self.engine.status().state == "ready"
            self.engine.load()
            if not was_ready:
                timing["load_seconds"] = perf_counter() - loading
            started = perf_counter()
            recognition = self.engine.recognize(audio, language, context)
            timing["inference_seconds"] = perf_counter() - started
        return recognition, timing

    def _result(self, text, language, normalized, duration, silent, timing, started):
        status = self.engine.status()
        return RecognitionResult(
            text=text,
            language=language,
            language_mode="hint" if normalized else "auto",
            duration_seconds=round(duration, 4),
            silent=silent,
            model=status.model_id,
            backend=status.backend,
            device=status.device,
            dtype=status.dtype,
            timing=Timing(
                **{key: round(value, 4) for key, value in timing.items()},
                total_seconds=round(perf_counter() - started, 4),
                rtf=round(timing["inference_seconds"] / duration, 4) if duration else 0,
            ),
        )

    def recognize(
        self, audio: AudioData, language: str | None = None, context: str = ""
    ) -> RecognitionResult:
        if audio.sample_rate != 16000:
            raise ASRError("invalid_audio", "识别 API 接收 16 kHz 单声道音频。")
        normalized = normalize_language(language)
        started = perf_counter()
        with self._admitted():
            recognition, timing = self._infer(audio, normalized, context)
            return self._result(
                recognition.text,
                recognition.language,
                normalized,
                audio.duration_seconds,
                audio.is_silent,
                timing,
                started,
            )

    def transcribe_file(
        self,
        source: AudioSource,
        language: str | None = None,
        context: str = "",
        *,
        on_segment: Callable[[FileProgress], None] | None = None,
        cancel: Cancellation | None = None,
        grouper: "SentenceGrouper | None" = None,
    ) -> TranscriptionResult:
        normalized = normalize_language(language)
        started = perf_counter()
        segments, sections = [], []
        grouper = grouper if grouper is not None else SemanticGrouper(self.options.semantic_seconds)
        timing, silent, detected, duration = empty_timing(), True, "unknown", 0.0
        with self._admitted():
            iterator = self.decoder.chunks(source, self.options.chunk_seconds)
            try:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise ASRError("cancelled", "已取消识别，当前片段结束后停止。")
                    decoding = perf_counter()
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        break
                    timing["decode_seconds"] += perf_counter() - decoding
                    recognition, chunk_timing = self._infer(
                        chunk.audio, normalized, context, cancel
                    )
                    for key, value in chunk_timing.items():
                        timing[key] += value
                    duration = chunk.end
                    silent = silent and chunk.audio.is_silent
                    if not chunk.audio.is_silent:
                        detected = recognition.language
                    segment = Segment(
                        id=len(segments),
                        start_seconds=round(chunk.start, 4),
                        end_seconds=round(chunk.end, 4),
                        text=recognition.text,
                        language=recognition.language,
                        boundary=chunk.boundary,
                    )
                    segments.append(segment)
                    completed = tuple(grouper.add(segment))
                    sections.extend(completed)
                    if on_segment:
                        on_segment(FileProgress(chunk.end, len(segments), segment, completed))
            finally:
                iterator.close()
            sections.extend(grouper.flush())
            result = self._result(
                "\n".join(section.text for section in sections),
                detected,
                normalized,
                duration,
                silent,
                timing,
                started,
            )
            return TranscriptionResult(
                **{field.name: getattr(result, field.name) for field in fields(result)},
                segments=tuple(segments),
                semantic_sections=tuple(sections),
                chunks_completed=len(segments),
            )

    def create_stream(
        self,
        language: str | None = None,
        context: str = "",
        *,
        options: "StreamOptions | None" = None,
        segmenter: "WindowSegmenter | None" = None,
        grouper: "SentenceGrouper | None" = None,
    ) -> "PCMStream":
        """Create an independent synchronous PCM session; the caller owns scheduling."""
        from .stream import PCMStream, StreamOptions

        return PCMStream(
            self,
            language,
            context,
            options=options
            if options is not None
            else StreamOptions(semantic_seconds=self.options.semantic_seconds),
            segmenter=segmenter,
            grouper=grouper,
        )

    def status(self) -> EngineStatus:
        return self.engine.status()

    def close(self):
        """Wait for active inference, release an explicitly closable engine, then reset warmup."""
        with self._inference_lock:
            close = getattr(self.engine, "close", None)
            if close is not None:
                close()
            self._warmed = False
