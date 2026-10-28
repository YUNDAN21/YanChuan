import threading

import numpy as np
import pytest
from livecaptions_audio.session import SessionRunner

from livecaptions_asr import EnergySegmenter, Recognizer
from livecaptions_asr.domain import AudioData, EngineStatus, Recognition


class Engine:
    def load(self):
        pass

    def status(self):
        return EngineStatus("test", state="ready")

    def recognize(self, audio, language, context):
        return Recognition("完整句子。", "Chinese")


class Capture:
    def __init__(self, samples, fail=False):
        self.samples = samples
        self.fail = fail
        self.opened = False
        self.stops = 0
        self.windows = []

    def start(self):
        self.opened = True
        if self.fail:
            raise RuntimeError("device failed")

    def status(self):
        return {"state": "stopped", "duration_seconds": len(self.samples) / 16000}

    def heartbeat(self):
        pass

    def window(self, start, end):
        self.windows.append((start, end))
        return AudioData.from_pcm16k(self.samples[round(start * 16000) : round(end * 16000)])

    def stop(self):
        self.stops += 1


def test_runner_preserves_short_tail_and_finalizes_semantics():
    pcm = np.concatenate([np.full(16000, 0.1, np.float32), np.zeros(11424, np.float32)])
    capture = Capture(pcm)
    runner = SessionRunner(Recognizer(Engine()), EnergySegmenter, lambda: capture)
    events, states = [], []
    runner.run("zh", threading.Event(), threading.Event(), events.extend, states.append)
    assert capture.windows == [(0, 1), (1, len(pcm) / 16000)]
    assert capture.stops == 1
    assert any(event.kind == "sentence" for event in events)
    assert states == ["preparing", "recording"]


def test_cancel_during_prepare_never_opens_capture_and_closes_segmenter():
    stop, cancel = threading.Event(), threading.Event()
    capture = Capture(np.ones(16000, np.float32))
    segmenter = EnergySegmenter()
    segmenter.close = lambda: setattr(segmenter, "released", True)

    class PreparingRecognizer:
        def prepare(self):
            cancel.set()

    runner = SessionRunner(PreparingRecognizer(), lambda: segmenter, lambda: capture)
    runner.run("auto", stop, cancel, lambda events: None, lambda state: None)
    assert not capture.opened
    assert segmenter.released


def test_capture_start_failure_releases_stream_and_device():
    capture = Capture(np.ones(16000, np.float32), fail=True)
    runner = SessionRunner(Recognizer(Engine()), EnergySegmenter, lambda: capture)
    progress = []
    with pytest.raises(RuntimeError, match="device failed"):
        runner.run(
            "auto",
            threading.Event(),
            threading.Event(),
            lambda events: None,
            lambda state: None,
            on_progress=progress.append,
        )
    assert capture.stops == 1
    assert progress[-1].stage == "audio_start" and progress[-1].state == "failed"


def test_stop_drains_remaining_pcm_and_confirms_the_tail_once():
    capture = Capture(np.ones(24224, np.float32) * 0.1)
    stop = threading.Event()
    events, states = [], []
    runner = SessionRunner(Recognizer(Engine()), EnergySegmenter, lambda: capture)

    def state(value):
        states.append(value)
        if value == "recording":
            stop.set()

    runner.run("zh", stop, threading.Event(), events.extend, state)
    assert states == ["preparing", "recording", "draining"]
    assert capture.stops == 1
    assert capture.windows[-1][-1] == len(capture.samples) / 16000
    assert sum(event.kind == "sentence" for event in events) == 1


def test_cancel_stops_capture_and_does_not_request_final_inference():
    cancel = threading.Event()
    capture = Capture(np.ones(64000, np.float32) * 0.1)
    events = []
    runner = SessionRunner(Recognizer(Engine()), EnergySegmenter, lambda: capture)

    def accept(batch):
        events.extend(batch)
        cancel.set()

    # Use an explicitly active source so the first partial triggers cancellation.
    capture.status = lambda: {"state": "recording", "duration_seconds": 1.3}
    runner.run("auto", threading.Event(), cancel, accept, lambda state: None)
    assert capture.stops == 1
    assert events and all(event.kind == "partial" for event in events)
