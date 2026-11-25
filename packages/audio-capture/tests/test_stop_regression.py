"""Device stop remains responsive while the recognition consumer is occupied."""

import threading
from types import SimpleNamespace

import numpy as np
from livecaptions_audio.session import SessionRunner

from livecaptions_asr.domain import AudioData


def test_stop_signals_device_while_inference_is_blocked():
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    stop = threading.Event()

    class Capture:
        def start(self):
            pass

        def heartbeat(self):
            pass

        def status(self):
            return {"state": "stopped" if stopped.is_set() else "recording", "duration_seconds": 1}

        def window(self, start, end):
            return AudioData.from_pcm16k(np.ones(round((end - start) * 16000), np.float32))

        def request_stop(self):
            stopped.set()

        def stop(self):
            stopped.set()

    class Stream:
        closed = False

        def feed(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return []

        def finish(self):
            self.closed = True
            return []

        def cancel(self):
            self.closed = True

    runner = SessionRunner(
        SimpleNamespace(prepare=lambda: None, create_stream=lambda **kw: Stream()),
        lambda: None,
        Capture,
    )
    thread = threading.Thread(
        target=runner.run, args=("auto", stop, threading.Event(), lambda _: None, lambda _: None)
    )
    thread.start()
    try:
        assert entered.wait(2)
        stop.set()
        assert stopped.wait(1), "Device stop must not wait for inference"
    finally:
        release.set()
        thread.join(3)
    assert stopped.is_set()
