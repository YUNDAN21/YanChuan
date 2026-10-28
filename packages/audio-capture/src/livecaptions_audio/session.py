"""Blocking session orchestration; no Qt, HTTP or concrete capture/model imports."""

import threading
from collections.abc import Callable
from typing import Protocol

from livecaptions_asr.domain import ASRError, AudioData
from livecaptions_asr.results import StreamEvent
from livecaptions_asr.startup import StartupObserver, StartupTimer


class Capture(Protocol):
    def start(self): ...
    def status(self) -> dict: ...
    def heartbeat(self): ...
    def window(self, start: float, end: float) -> AudioData: ...
    def stop(self): ...


class SessionRunner:
    """One consumer per PCM stream; capture opens only after model preparation."""

    def __init__(
        self, recognizer, segmenter_factory: Callable, capture_factory: Callable[[], Capture]
    ):
        self.recognizer = recognizer
        self.segmenter_factory = segmenter_factory
        self.capture_factory = capture_factory

    def run(
        self,
        language: str,
        stop: threading.Event,
        cancel: threading.Event,
        on_events: Callable[[list[StreamEvent]], None],
        on_state: Callable[[str], None],
        *,
        on_progress: StartupObserver | None = None,
    ):
        timer = StartupTimer(on_progress)
        capture = stream = segmenter = None
        capture_stopped = False
        try:
            on_state("preparing")
            with timer.phase("segmentation_prepare"):
                segmenter = self.segmenter_factory()
            if stop.is_set() or cancel.is_set():
                return
            if on_progress is None:
                self.recognizer.prepare()
            else:
                self.recognizer.prepare(on_progress=on_progress)
            if stop.is_set() or cancel.is_set():
                return
            with timer.phase("stream_setup"):
                stream = self.recognizer.create_stream(language=language, segmenter=segmenter)
                capture = self.capture_factory()
            if stop.is_set() or cancel.is_set():
                capture = None
                return
            with timer.phase("audio_start"):
                capture.start()
            on_state("recording")
            cursor = 0.0
            while True:
                capture.heartbeat()
                if cancel.is_set():
                    return
                if stop.is_set() and not capture_stopped:
                    on_state("draining")
                    capture.stop()
                    capture_stopped = True
                status = capture.status()
                if status["state"] == "error":
                    raise ASRError("capture_failed", "音频采集失败，请检查所选设备。")
                finished = status["state"] == "stopped"
                available = max(0, status["duration_seconds"] - (0 if finished else 0.15))
                if not finished:
                    available = int(available * 10) / 10
                if available > cursor + 1e-5:
                    end = min(available, cursor + 1)
                    audio = capture.window(cursor, end)
                    events = list(stream.feed(audio.samples, emit_partial=available - cursor < 2))
                    if events:
                        on_events(events)
                    cursor = end
                    continue
                if finished:
                    if not cancel.is_set():
                        on_events(list(stream.finish()))
                    return
                stop.wait(0.05)
        finally:
            # Cancellation and failures stop devices without additional inference.
            try:
                if capture is not None and not capture_stopped:
                    capture.stop()
            finally:
                if stream is not None and not stream.closed:
                    stream.cancel()
                elif (
                    stream is None
                    and segmenter is not None
                    and (close := getattr(segmenter, "close", None))
                ):
                    close()
