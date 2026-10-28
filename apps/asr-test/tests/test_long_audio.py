import io
import json
import threading
from collections import deque
from dataclasses import replace
from time import monotonic

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from livecaptions_audio.mixer import CapturedBlock
from livecaptions_audio.wasapi import WasapiRecording
from livecaptions_testapp.api import create_app
from livecaptions_testapp.archive import AudioArchive, wav_header
from livecaptions_testapp.archive_response import archive_response
from livecaptions_testapp.config import Settings
from livecaptions_testapp.jobs import TranscriptionJobs

from livecaptions_asr import Recognizer
from livecaptions_asr.domain import ASRError, AudioData, EngineStatus, Recognition


class MeasuringEngine:
    def __init__(self):
        self.info = EngineStatus("test-model", backend="test")
        self.windows = []

    def status(self):
        return self.info

    def load(self):
        if self.info.state != "ready":
            self.info = replace(self.info, state="ready", load_count=1, device="test")

    def recognize(self, audio, language, context):
        self.windows.append(audio.duration_seconds)
        return Recognition(f"第{len(self.windows)}句。", language or "Chinese")


def make_audio(path, seconds=145, channels=1, rate=16000):
    block = (0.1 * np.sin(np.arange(rate) * 2 * np.pi * 440 / rate)).astype("float32")
    if channels > 1:
        block = np.repeat(block[:, None], channels, axis=1)
    with sf.SoundFile(path, "w", samplerate=rate, channels=channels, subtype="PCM_16") as target:
        for _ in range(seconds):
            target.write(block)
    return path


def wait_job(http, job_id):
    deadline = monotonic() + 10
    while True:
        result = http.get(f"/v1/transcription-jobs/{job_id}").json()
        if result["state"] in {"completed", "error", "cancelled"}:
            return result
        assert monotonic() < deadline
        threading.Event().wait(0.01)


def test_upload_over_previous_size_and_duration_limits(tmp_path):
    path = make_audio(tmp_path / "large.wav", seconds=180, channels=2, rate=48000)
    assert path.stat().st_size > 32 * 1024 * 1024
    engine = MeasuringEngine()
    config = Settings(_env_file=None, data_dir=tmp_path / "data")
    assert config.max_upload_bytes is None and config.max_audio_seconds is None
    with TestClient(create_app(config, engine)) as http, path.open("rb") as source:
        submitted = http.post("/v1/transcription-jobs", files={"file": ("large.wav", source)})
        assert submitted.status_code == 202, submitted.text
        result = wait_job(http, submitted.json()["id"])
        assert result["state"] == "completed", result
        assert result["processed_seconds"] == 180
        assert result["chunks_completed"] > 1 and max(engine.windows) <= 20.1
    assert list((tmp_path / "data/uploads").iterdir()) == []


def test_file_cancellation_releases_capacity_and_temporary_file(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingEngine(MeasuringEngine):
        def recognize(self, audio, language, context):
            entered.set()
            assert release.wait(5)
            return super().recognize(audio, language, context)

    path = make_audio(tmp_path / "cancel.wav", 65)
    engine = BlockingEngine()
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), engine)
    ) as http:
        with path.open("rb") as source:
            submitted = http.post("/v1/transcription-jobs", files={"file": ("cancel.wav", source)})
        job_id = submitted.json()["id"]
        try:
            assert entered.wait(5)
            assert http.get("/health").status_code == 200
            http.post(f"/v1/transcription-jobs/{job_id}/cancel").raise_for_status()
        finally:
            release.set()
        state = wait_job(http, job_id)
        assert state["state"] == "cancelled" and len(engine.windows) == 1
    assert list((tmp_path / "data/uploads").iterdir()) == []


def test_archive_volumes_and_semantic_wavs_have_no_missing_samples(tmp_path):
    archive = AudioArchive(tmp_path / "archive", chunk_seconds=5)
    samples = np.linspace(-0.5, 0.5, 13 * 16000, dtype="float32")
    for offset in range(0, len(samples), 16000):
        archive.append(samples[offset : offset + 16000])
    archive.section(
        {
            "id": 0,
            "start_seconds": 1,
            "end_seconds": 8,
            "text": "一个完整句子。",
            "boundary": "sentence_and_pause",
            "segment_ids": [0, 1],
        }
    )
    archive.finish()
    manifest = json.loads((archive.directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["frames"] == len(samples)
    assert len(manifest["source_parts"]) == 3
    full, rate = sf.read(io.BytesIO(b"".join(archive.iter_wav())), dtype="float32")
    assert rate == 16000 and len(full) == len(samples)
    np.testing.assert_allclose(full, samples, atol=2 / 32768)
    section, _ = sf.read(archive.directory / "sentence-000000.wav", dtype="float32")
    np.testing.assert_allclose(section, full[16000 : 8 * 16000], atol=1e-6)


def test_long_capture_audio_buffer_stays_bounded_and_old_audio_uses_archive(tmp_path, monkeypatch):
    archive = AudioArchive(tmp_path / "capture", chunk_seconds=30)
    capture = WasapiRecording("system", None, None, None, buffer_seconds=30, archive=archive)
    capture.started = 0
    blocks = deque()
    capture._tracks = [blocks]
    clock = [0.0]
    monkeypatch.setattr("livecaptions_audio.wasapi.perf_counter", lambda: clock[0])
    samples = np.full(16000, 0.1, dtype="float32")
    # Simulate ten minutes without waiting ten minutes or opening any hardware.
    for second in range(600):
        clock[0] = second + 1
        capture._append_block(blocks, CapturedBlock(second, 16000, samples.copy()))
        capture._persist(second + 1)
        assert len(blocks) <= 32
    assert capture.status()["limit_seconds"] is None
    old = capture.window(0, 1).samples
    assert old.mean() == pytest.approx(0.1, abs=0.001)
    capture.archive = None
    with pytest.raises(ASRError) as error:
        capture.window(0, 1)
    assert error.value.code == "capture_backlog"
    archive.finish()
    assert archive.frames == 600 * 16000


def test_archive_byte_ranges_and_rf64_header(tmp_path):
    from fastapi import FastAPI, Request

    archive = AudioArchive(tmp_path / "ranges")
    archive.append(np.ones(16000, dtype="float32") * 0.1)
    archive.finish()
    app = FastAPI()

    @app.get("/audio")
    def audio(request: Request):
        return archive_response(archive, request)

    with TestClient(app) as http:
        full = http.get("/audio").content
        part = http.get("/audio", headers={"Range": "bytes=30-100"})
        assert part.status_code == 206 and part.content == full[30:101]
        assert http.get("/audio", headers={"Range": "bytes=-10"}).content == full[-10:]
        assert http.get("/audio", headers={"Range": "bytes=999999-"}).status_code == 416
    assert wav_header(3_000_000_000).startswith(b"RF64")


def test_client_disconnection_stops_unlimited_capture_and_releases_device(monkeypatch):
    from types import SimpleNamespace

    clock = [0.0]
    closed = []

    class Stream:
        def start_stream(self):
            pass

        def stop_stream(self):
            closed.append("stopped")

        def close(self):
            closed.append("closed")

    class PortAudio:
        def get_host_api_info_by_type(self, kind):
            return {"index": 0, "defaultInputDevice": 0, "defaultOutputDevice": 1}

        def get_device_count(self):
            return 3

        def get_device_info_by_index(self, index):
            return {
                "index": index,
                "hostApi": 0,
                "maxInputChannels": 0 if index == 1 else 1,
                "name": "output [Loopback]" if index == 2 else "output",
                "isLoopbackDevice": index == 2,
                "defaultSampleRate": 16000,
            }

        def open(self, **kwargs):
            return Stream()

        def terminate(self):
            closed.append("terminated")

    class AdvancingEvent:
        def wait(self, timeout):
            clock[0] += 10
            return False

        def set(self):
            pass

    module = SimpleNamespace(PyAudio=PortAudio, paWASAPI=1, paFloat32=2)
    monkeypatch.setattr("livecaptions_audio.wasapi._pyaudio", lambda: module)
    monkeypatch.setattr("livecaptions_audio.wasapi.perf_counter", lambda: clock[0])
    capture = WasapiRecording("system", None, None, None, idle_seconds=5)
    capture.stop_event = AdvancingEvent()
    capture._run()
    assert capture.done_event.is_set() and capture.error is None
    assert capture.duration == 10 and capture.limit is None
    assert "客户端失联" in capture.warnings[-1]
    assert closed == ["stopped", "closed", "terminated"]


def test_file_job_queue_is_bounded_and_live_capacity_is_available(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingEngine(MeasuringEngine):
        def recognize(self, audio, language, context):
            entered.set()
            assert release.wait(5)
            return super().recognize(audio, language, context)

    source = make_audio(tmp_path / "queue.wav", 2).read_bytes()
    asr = Recognizer(BlockingEngine())
    jobs = TranscriptionJobs(asr, tmp_path / "uploads")
    try:
        first = jobs.submit(io.BytesIO(source), "one.wav")
        assert entered.wait(5)
        second = jobs.submit(io.BytesIO(source), "two.wav")
        assert jobs.find(second["id"]).state == "queued"
        # The running file occupies one slot; the queued file occupies none.
        assert asr.recognize(AudioData.from_pcm16k(np.zeros(16000))).silent is True
        with pytest.raises(ASRError) as exc:
            jobs.submit(io.BytesIO(source), "three.wav")
        assert exc.value.code == "server_busy"
        jobs.find(second["id"]).cancel.set()
        release.set()
        assert jobs.find(first["id"]).done.wait(5)
        assert jobs.find(second["id"]).done.wait(5)
    finally:
        release.set()
        jobs.close()
    assert list((tmp_path / "uploads").iterdir()) == []
