"""Device transitions with synthetic callbacks only: no hardware capture or playback."""

import threading
from types import SimpleNamespace

import numpy as np
import pytest
from livecaptions_audio import WasapiRecording, wasapi
from livecaptions_audio.mixer import CapturedBlock, mix_samples


class Stream:
    def __init__(self, owner, kind, callback):
        self.owner, self.kind, self.callback = owner, kind, callback
        self.closed = self.stopped = False

    def start_stream(self):
        self.owner.operations.append(("start", self.kind, threading.get_ident()))
        if self.kind == self.owner.block_kind:
            self.owner.opening.set()
            assert self.owner.release.wait(3)
        if self.kind == self.owner.fail_kind:
            raise OSError("device denied")

    def stop_stream(self):
        self.owner.operations.append(("stop", self.kind, threading.get_ident()))
        self.stopped = True

    def close(self):
        self.owner.operations.append(("close", self.kind, threading.get_ident()))
        self.closed = True

    def emit(self, value, seconds=0.1):
        pcm = np.full(round(seconds * 16000), value, dtype=np.float32)
        self.callback(pcm.tobytes(), len(pcm), {}, 0)


@pytest.fixture
def devices(monkeypatch):
    class Audio:
        def __init__(self):
            self.streams, self.operations = [], []
            self.fail_kind = self.block_kind = None
            self.opening, self.release = threading.Event(), threading.Event()
            self.terminated = False
            self.clock = 100.0
            self.available = [
                {"kind": kind, "id": str(i), "default": True, "sample_rate": 16000, "channels": 1}
                for i, kind in enumerate(("microphone", "system"))
            ]

        def open(self, **params):
            kind = ("microphone", "system")[params["input_device_index"]]
            self.operations.append(("open", kind, threading.get_ident()))
            stream = Stream(self, kind, params["stream_callback"])
            self.streams.append(stream)
            return stream

        def terminate(self):
            self.operations.append(("terminate", "", threading.get_ident()))
            self.terminated = True

        def latest(self, kind):
            return next(s for s in reversed(self.streams) if s.kind == kind)

    audio = Audio()
    module = SimpleNamespace(PyAudio=lambda: audio, paFloat32=1, paComplete=2, paContinue=3)
    monkeypatch.setattr(wasapi, "_pyaudio", lambda: module)
    monkeypatch.setattr(wasapi, "_devices", lambda *_: audio.available)
    monkeypatch.setattr(wasapi, "perf_counter", lambda: audio.clock)
    return audio


def test_switch_preserves_timeline_pcm_gain_and_single_owner(devices):
    capture = WasapiRecording("system", None, None, None)
    capture.start()
    try:
        devices.clock = 100.1
        devices.latest("system").emit(0.8)
        np.testing.assert_allclose(capture.window(0, 0.1).samples, 0.8, atol=1e-6)
        assert [s.kind for s in devices.streams] == ["system"]  # unopened mic stays off
        origin = capture.started
        assert capture.set_source("both").result(3) == "both"
        assert not devices.latest("system").stopped  # retained input never restarts
        devices.clock = 100.2
        devices.latest("system").emit(0.8)
        devices.latest("microphone").emit(0.4)
        np.testing.assert_allclose(capture.window(0.11, 0.19).samples, 0.6, atol=1e-6)
        assert capture.set_source("microphone").result(3) == "microphone"
        assert devices.latest("system").closed
        devices.clock = 100.3
        devices.latest("microphone").emit(0.4)
        devices.latest("system").emit(1)  # late callback from a retired input is ignored
        np.testing.assert_allclose(capture.window(0.21, 0.29).samples, 0.4, atol=1e-6)
        for source in ("system", "both", "microphone", "system"):
            assert capture.set_source(source).result(3) == source
        assert len(capture._tracks) == 2
        assert capture.started == origin
        assert capture.status()["duration_seconds"] == 0.3
        # Reading earlier PCM after several transitions gives the same amplitudes.
        np.testing.assert_allclose(capture.window(0, 0.1).samples, 0.8, atol=1e-6)
    finally:
        capture.stop()
    assert all(s.closed for s in devices.streams)
    assert devices.terminated
    assert {op[2] for op in devices.operations} == {capture.thread.ident}
    with pytest.raises(RuntimeError, match="正在采集"):
        capture.set_source("both")


def test_failed_new_input_keeps_existing_capture_and_allows_retry(devices):
    capture = WasapiRecording("system", None, None, None)
    capture.start()
    try:
        devices.fail_kind = "microphone"
        with pytest.raises(Exception, match="device denied"):
            capture.set_source("both").result(3)
        assert capture.source == "system" and capture.error is None
        assert devices.latest("microphone").closed
        assert not devices.latest("system").closed
        devices.fail_kind = None
        assert capture.set_source("both").result(3) == "both"
    finally:
        capture.stop()


def test_stop_while_device_opens_settles_every_request(devices):
    capture = WasapiRecording("system", None, None, None)
    capture.start()
    devices.block_kind = "microphone"
    first = capture.set_source("both")
    assert devices.opening.wait(3)
    second = capture.set_source("microphone")
    try:
        capture.stop_event.set()
    finally:
        devices.release.set()
        capture.stop()
    for request in (first, second):
        with pytest.raises(Exception, match="结束"):
            request.result(1)
    assert capture.source == "system"
    assert all(s.closed for s in devices.streams)


def test_new_callback_does_not_rewrite_before_switch_and_disabled_buffers_expire(devices):
    capture = WasapiRecording("system", None, None, None, buffer_seconds=2)
    capture.start()
    try:
        devices.clock = 100.1
        devices.latest("system").emit(0.8)
        devices.clock = 100.15
        capture.set_source("microphone").result(3)
        devices.clock = 100.2
        devices.latest("microphone").emit(0.4, seconds=0.2)
        np.testing.assert_allclose(capture.window(0, 0.1).samples, 0.8, atol=1e-6)
        np.testing.assert_allclose(capture.window(0.1, 0.14).samples, 0, atol=1e-6)
        np.testing.assert_allclose(capture.window(0.16, 0.19).samples, 0.4, atol=1e-6)
        devices.clock = 104
        devices.latest("microphone").emit(0.4)
        assert len(capture._source_tracks["system"]) == 0
        assert len(capture._source_tracks["microphone"]) == 1
    finally:
        capture.stop()


def test_invalid_or_missing_source_does_not_replace_selected_input(devices):
    capture = WasapiRecording("system", "missing-mic", None, None)
    capture.start()
    try:
        with pytest.raises(ValueError):
            capture.set_source("unknown")
        with pytest.raises(Exception, match="麦克风"):
            capture.set_source("microphone").result(3)
        assert capture.source == "system" and capture.status()["state"] == "recording"
        count = len(devices.streams)
        capture.set_source("system").result(3)
        assert len(devices.streams) == count
    finally:
        capture.stop()


def test_active_mix_counts_sources_at_each_sample_including_silent_source():
    samples = lambda value: np.full(1600, value, dtype=np.float32)
    tracks = [
        [CapturedBlock(0, 16000, samples(0.8)), CapturedBlock(0.1, 16000, samples(0.8))],
        [CapturedBlock(0.1, 16000, samples(0))],
    ]
    actual = mix_samples(tracks, 0.2, active_gain=True)
    np.testing.assert_allclose(actual[:1600], 0.8)
    np.testing.assert_allclose(actual[1600:], 0.4)
