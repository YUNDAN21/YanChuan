import json
import threading

import numpy as np
from livecaptions_audio.session import SessionRunner
from livecaptions_service.startup_trace import StartupTrace

from livecaptions_asr import AudioData, EnergySegmenter, Recognizer
from livecaptions_asr.domain import EngineStatus, Recognition
from livecaptions_asr.startup import StartupEvent


def test_trace_appends_attempts_and_finishes_at_recording_once(tmp_path):
    path = tmp_path / "data/startup.jsonl"
    events, states = [], []
    trace = StartupTrace(path, events.append)
    trace.emit(StartupEvent("weights_load", "started"))
    trace.emit(StartupEvent("weights_load", "completed", 1.25))
    state = trace.state_handler(states.append)
    state("recording")
    trace.emit(StartupEvent("qwen_model", "reused"))
    trace.finish("failed")
    trace.finish("cancelled")
    other = StartupTrace(path, events.append)
    other.finish("cancelled")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 6
    assert records[0]["attempt"] != records[-1]["attempt"]
    assert states == ["recording"]
    assert [event.state for event in events if event.stage == "startup_total"] == [
        "started",
        "completed",
        "started",
        "cancelled",
    ]
    assert all("timestamp" in record and record["elapsed_seconds"] >= 0 for record in records)


def test_startup_trace_storage_failure_still_reports_progress(tmp_path, caplog):
    directory = tmp_path / "blocked"
    directory.mkdir()
    events = []
    trace = StartupTrace(directory, events.append)
    trace.finish()
    assert events[-1].stage == "startup_total" and events[-1].state == "completed"
    assert "Cannot write startup timing log" in caplog.text


def test_runner_reports_device_start_after_warmup_and_before_recording():
    order, progress = [], []

    class Engine:
        def load(self):
            order.append("load")

        def recognize(self, *args):
            order.append("warmup")
            return Recognition("", "unknown")

        def status(self):
            return EngineStatus("test", state="ready")

    class Capture:
        def start(self):
            order.append("device")

        def stop(self):
            pass

        def heartbeat(self):
            pass

        def status(self):
            return {"state": "stopped", "duration_seconds": 0.1}

        def window(self, *args):
            return AudioData.from_pcm16k(np.zeros(1600, np.float32))

    runner = SessionRunner(Recognizer(Engine()), EnergySegmenter, Capture)
    runner.run(
        "auto",
        threading.Event(),
        threading.Event(),
        lambda batch: None,
        order.append,
        on_progress=progress.append,
    )
    assert order.index("warmup") < order.index("device") < order.index("recording")
    assert [event.stage for event in progress if event.state == "completed"][-2:] == [
        "stream_setup",
        "audio_start",
    ]
