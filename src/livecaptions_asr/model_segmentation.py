"""Model-controlled recognition windows; no devices, services or model loading."""

from collections import deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .domain import ASRError, validated_pcm
from .endpoints.protocols import EndpointDetector, SpeechDetector
from .segmentation import Window

RATE = 16000


@dataclass(frozen=True)
class EndpointOptions:
    speech_threshold: float = 0.5
    endpoint_threshold: float = 0.5
    silence_seconds: float = 0.6
    max_wait_seconds: float = 2.0
    max_segment_seconds: float = 16.0
    update_seconds: float = 1.0
    pre_roll_seconds: float = 0.2
    post_roll_seconds: float = 0.2

    def __post_init__(self):
        if not all(np.isfinite(v) and v > 0 for v in vars(self).values()):
            raise ASRError("invalid_options", "端点参数必须是有限正数。")
        if not 0 < self.speech_threshold <= 1 or not 0 < self.endpoint_threshold <= 1:
            raise ASRError("invalid_options", "概率阈值必须在 (0, 1]。")
        if self.max_wait_seconds < self.silence_seconds:
            raise ASRError("invalid_options", "最长静音等待不能短于候选停顿。")
        if self.post_roll_seconds > self.silence_seconds or self.max_segment_seconds < 0.1:
            raise ASRError("invalid_options", "尾部保留不能超过候选停顿，段长至少为 0.1 秒。")


class ModelSegmenter:
    """VAD proposes a pause; endpoint probability accepts or rejects that cut.

    A rejection keeps the same segment, including speech after the pause. Probe
    once per pause, not on every silent frame. Timeouts and maximum duration are
    explicit fallbacks. Input is contiguous PCM, with a timeline starting at zero.
    One instance owns one speech detector's state; endpoint detectors may be shared.
    """

    def __init__(
        self,
        speech: SpeechDetector,
        endpoint: EndpointDetector,
        options: EndpointOptions | None = None,
    ):
        self.options = options or EndpointOptions()
        if (
            not isinstance(speech.frame_samples, int)
            or isinstance(speech.frame_samples, bool)
            or not 1 <= speech.frame_samples <= RATE
        ):
            raise ASRError("invalid_options", "人声检测帧长必须在 1–16000 样本之间。")
        if (
            not isinstance(endpoint.context_samples, int)
            or isinstance(endpoint.context_samples, bool)
            or endpoint.context_samples < 1
        ):
            raise ASRError("invalid_options", "端点上下文必须是正整数样本数。")
        self.context_samples = endpoint.context_samples
        if self.options.pre_roll_seconds * RATE > self.context_samples:
            raise ASRError("invalid_options", "前置音频不能超过端点模型上下文。")
        self.speech, self.endpoint = speech, endpoint
        self.start: float | None = None
        self.segment_id = 0
        self.last_voice = 0.0
        self.last_update = 0.0
        self.confirmed_end = 0.0
        self.last_endpoint_probability: float | None = None
        self.endpoint_calls = 0
        self._checked_pause = False
        self._received = 0
        self._processed = 0
        self._pending = np.empty(0, dtype=np.float32)
        self._history: deque[tuple[int, npt.NDArray[np.float32]]] = deque()
        self._closed = False

    @property
    def lookback_seconds(self) -> float:
        # A VAD frame may straddle two calls to feed(). Keep that extra lookback.
        return self.options.pre_roll_seconds + self.speech.frame_samples / RATE

    @property
    def buffered_seconds(self) -> float:
        return (sum(len(pcm) for _, pcm in self._history) + len(self._pending)) / RATE

    @staticmethod
    def _probability(value: float) -> float:
        if not np.isscalar(value) or not np.isfinite(value) or not 0 <= value <= 1:
            raise ASRError("invalid_model_output", "检测模型必须返回 [0, 1] 内的概率。")
        return float(value)

    def _remember(self, pcm):
        self._history.append((self._processed, pcm.copy()))
        before = self._processed + len(pcm) - self.context_samples
        while self._history and self._history[0][0] + len(self._history[0][1]) <= before:
            self._history.popleft()
        if self._history and self._history[0][0] < before:
            offset, block = self._history.popleft()
            self._history.appendleft((before, block[before - offset :].copy()))

    def _context(self):
        begin = max(round(self.start * RATE), self._processed - self.context_samples)
        return np.concatenate(
            [
                pcm[max(0, begin - offset) :]
                for offset, pcm in self._history
                if offset + len(pcm) > begin
            ]
        )

    def _finish(self, end, boundary):
        window = Window(self.segment_id, self.start, end, True, boundary)
        self.segment_id += 1
        self.confirmed_end = end
        self.start = None
        self._checked_pause = False
        return window

    def _frame(self, pcm):
        begin, end = self._processed / RATE, (self._processed + len(pcm)) / RATE
        self._remember(pcm)
        self._processed += len(pcm)
        voiced = self._probability(self.speech.probability(pcm)) >= self.options.speech_threshold
        if voiced:
            if self.start is None:
                self.start = max(self.confirmed_end, begin - self.options.pre_roll_seconds)
                self.last_update = self.start
            self.last_voice = end
            self._checked_pause = False
        if self.start is None:
            return None
        if end - self.start >= self.options.max_segment_seconds - 1e-9:
            return self._finish(end, "max_duration")
        quiet = end - self.last_voice
        if quiet >= self.options.silence_seconds - 1e-9 and not self._checked_pause:
            self.last_endpoint_probability = self._probability(
                self.endpoint.probability(self._context())
            )
            self.endpoint_calls += 1
            self._checked_pause = True
            if self.last_endpoint_probability >= self.options.endpoint_threshold:
                return self._finish(
                    min(end, self.last_voice + self.options.post_roll_seconds), "semantic_endpoint"
                )
        if quiet >= self.options.max_wait_seconds - 1e-9:
            return self._finish(
                min(end, self.last_voice + self.options.post_roll_seconds), "silence_timeout"
            )
        return None

    def feed(self, samples, offset):
        if self._closed:
            raise ASRError("stream_closed", "分段器已经结束。")
        if not np.isfinite(offset) or abs(offset * RATE - self._received) > 0.5:
            raise ASRError("invalid_audio_window", "模型分段器需要从零开始的连续音频。")
        samples = validated_pcm(samples, allow_empty=True)
        self._received += len(samples)
        finals = []
        cursor = 0
        while cursor < len(samples):
            count = min(self.speech.frame_samples - len(self._pending), len(samples) - cursor)
            self._pending = np.concatenate((self._pending, samples[cursor : cursor + count]))
            cursor += count
            if len(self._pending) == self.speech.frame_samples:
                frame, self._pending = self._pending, np.empty(0, dtype=np.float32)
                if window := self._frame(frame):
                    finals.append(window)
        return finals

    def partial(self, end):
        if self.start is None or end - self.last_update < self.options.update_seconds - 1e-9:
            return None
        self.last_update = end
        return Window(self.segment_id, self.start, end, False)

    def flush(self, end):
        # Stop is authoritative: do not run another endpoint model to approve it.
        window = self._finish(end, "end") if self.start is not None and end > self.start else None
        self.close()
        return window

    def close(self):
        """Release audio/state on normal finish, cancellation or a failed iterator."""
        if not self._closed:
            self.speech.reset()
        self._closed = True
        self.start = None
        self._history.clear()
        self._pending = np.empty(0, dtype=np.float32)
