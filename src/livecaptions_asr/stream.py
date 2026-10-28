"""Synchronous, bounded PCM -> recognition events. Caller owns I/O and scheduling."""

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from .domain import ASRError, AudioData, normalize_language, validated_pcm
from .results import RecognitionResult, Segment, Sentence, StreamEvent
from .segmentation import EnergySegmenter, Window
from .semantic import SemanticGrouper

RATE = 16000
FRAME = 1600


class WindowRecognizer(Protocol):
    def recognize(
        self, audio: AudioData, language: str | None, context: str
    ) -> RecognitionResult: ...


class WindowSegmenter(Protocol):
    start: float | None

    def feed(self, samples: npt.NDArray[np.float32], offset: float) -> list[Window]: ...
    def partial(self, end: float) -> Window | None: ...
    def flush(self, end: float) -> Window | None: ...


class SentenceGrouper(Protocol):
    def add(self, segment: Segment) -> list[Sentence]: ...
    def flush(self) -> list[Sentence]: ...


@dataclass(frozen=True)
class StreamOptions:
    threshold: float = 0.003
    silence_seconds: float = 0.6
    max_segment_seconds: float = 8
    update_seconds: float = 1
    semantic_seconds: float = 60

    def __post_init__(self):
        if not all(np.isfinite(value) and value > 0 for value in vars(self).values()):
            raise ASRError("invalid_options", "分段参数必须是有限正数。")
        if self.max_segment_seconds < 0.1:
            raise ASRError("invalid_options", "流识别窗口至少需要 0.1 秒。")


class PCMStream:
    """Accept contiguous mono float PCM at 16 kHz, with time starting at zero.

    Fully consume each returned iterator before the next call. Calls are synchronous
    and single-consumer; multiple streams may share a Recognizer. No device, clock,
    thread, file, heartbeat, transport or result-history ownership lives here.
    """

    def __init__(
        self,
        recognizer: WindowRecognizer,
        language=None,
        context="",
        *,
        options: StreamOptions | None = None,
        segmenter: WindowSegmenter | None = None,
        grouper: SentenceGrouper | None = None,
    ):
        normalize_language(language)
        self.recognizer, self.language, self.context = recognizer, language, context
        self.options = options or StreamOptions()
        self.segmenter = (
            segmenter
            if segmenter is not None
            else EnergySegmenter(
                self.options.threshold,
                self.options.silence_seconds,
                self.options.max_segment_seconds,
                self.options.update_seconds,
            )
        )
        self.grouper = (
            grouper if grouper is not None else SemanticGrouper(self.options.semantic_seconds)
        )
        self._blocks: deque[tuple[int, npt.NDArray[np.float32]]] = deque()
        self._pending = np.empty(0, dtype=np.float32)
        self._processed = 0
        self._received = 0
        self._revision = 0
        self._closed = False
        self._busy = False

    @property
    def duration_seconds(self) -> float:
        return self._received / RATE

    @property
    def buffered_seconds(self) -> float:
        return (sum(len(samples) for _, samples in self._blocks) + len(self._pending)) / RATE

    @property
    def closed(self) -> bool:
        return self._closed

    @contextmanager
    def _exclusive(self):
        if self._busy:
            raise ASRError("stream_busy", "请先完整消费上一批事件，再继续操作会话。")
        self._busy = True
        try:
            yield
        except BaseException:
            self._discard()
            raise
        finally:
            self._busy = False

    def _discard(self):
        self._closed = True
        if close := getattr(self.segmenter, "close", None):
            close()
        self._blocks.clear()
        self._pending = np.empty(0, dtype=np.float32)
        # Drop any pending semantic text as well, without emitting or saving it.
        self.grouper.flush()

    def cancel(self) -> None:
        """Discard the unfinished sentence without another model call."""
        with self._exclusive():
            self._discard()

    def _audio(self, start: float, end: float) -> AudioData:
        begin, stop = round(start * RATE), round(end * RATE)
        parts = [
            samples[max(0, begin - offset) : min(len(samples), stop - offset)]
            for offset, samples in self._blocks
            if offset < stop and offset + len(samples) > begin
        ]
        if not parts or sum(map(len, parts)) != stop - begin:
            raise ASRError("invalid_audio_window", "分段器请求了已释放或尚未输入的音频。")
        return AudioData.from_pcm16k(np.concatenate(parts))

    def _recognize(self, window: Window) -> Iterator[StreamEvent]:
        if window.end - window.start < 0.1 - 1e-6:
            return
        result = self.recognizer.recognize(
            self._audio(window.start, window.end), self.language, self.context
        )
        self._revision += 1
        segment = Segment(
            window.segment_id,
            round(window.start, 4),
            round(window.end, 4),
            result.text,
            result.language,
            window.final,
            window.boundary,
            self._revision,
            result.timing.inference_seconds,
        )
        yield StreamEvent("final" if window.final else "partial", segment=segment)
        if window.final:
            for sentence in self.grouper.add(segment):
                yield StreamEvent("sentence", sentence=sentence)

    def _frame(self, samples: npt.NDArray[np.float32], emit_partial: bool) -> Iterator[StreamEvent]:
        self._blocks.append((self._processed, samples.copy()))
        windows = self.segmenter.feed(samples, self._processed / RATE)
        self._processed += len(samples)
        for window in windows:
            yield from self._recognize(window)
        if emit_partial and (window := self.segmenter.partial(self._processed / RATE)):
            yield from self._recognize(window)
        # Model VAD frames may straddle feeds; honor their bounded lookback.
        before = (
            round(self.segmenter.start * RATE)
            if self.segmenter.start is not None
            else max(
                0, self._processed - round(getattr(self.segmenter, "lookback_seconds", 0.2) * RATE)
            )
        )
        while self._blocks and self._blocks[0][0] + len(self._blocks[0][1]) <= before:
            self._blocks.popleft()
        if self._blocks and self._blocks[0][0] < before:
            offset, block = self._blocks.popleft()
            self._blocks.appendleft((before, block[before - offset :].copy()))

    def feed(self, pcm: npt.ArrayLike, *, emit_partial: bool = True) -> Iterator[StreamEvent]:
        """Yield updates as input is consumed; keep no growing event history.

        Callers may suppress partial inference while catching up. Final segments
        and sentence events are still emitted. Arbitrary input block sizes are
        reframed internally, so packet boundaries do not change RMS decisions.
        """
        if self._closed:
            raise ASRError("stream_closed", "增量会话已经结束。")
        samples = validated_pcm(pcm, allow_empty=True)
        with self._exclusive():
            self._received += len(samples)
            offset = 0
            while offset < len(samples):
                count = min(FRAME - len(self._pending), len(samples) - offset)
                self._pending = np.concatenate((self._pending, samples[offset : offset + count]))
                offset += count
                if len(self._pending) == FRAME:
                    frame, self._pending = self._pending, np.empty(0, dtype=np.float32)
                    yield from self._frame(frame, emit_partial)

    def finish(self) -> Iterator[StreamEvent]:
        """Consume the short tail and finalize remaining text. Idempotent."""
        with self._exclusive():
            if self._closed:
                return
            if len(self._pending):
                tail, self._pending = self._pending, np.empty(0, dtype=np.float32)
                yield from self._frame(tail, False)
            if window := self.segmenter.flush(self._processed / RATE):
                yield from self._recognize(window)
            for sentence in self.grouper.flush():
                yield StreamEvent("sentence", sentence=sentence)
            if close := getattr(self.segmenter, "close", None):
                close()
            self._closed = True
            self._blocks.clear()
