import io
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import monotonic

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from livecaptions_testapp.api import create_app
from livecaptions_testapp.capture.service import RecordingService
from livecaptions_testapp.config import Settings

from livecaptions_asr.domain import ASRError, AudioData, EngineStatus, Recognition


def wave(seconds=0.5, sample_rate=16000, silent=False, channels=1):
    samples = np.zeros(int(seconds * sample_rate), dtype=np.float32)
    if not silent:
        samples = (0.1 * np.sin(np.arange(len(samples)) * 2 * np.pi * 440 / sample_rate)).astype(
            "float32"
        )
    if channels > 1:
        samples = np.repeat(samples[:, None], channels, axis=1)
    output = io.BytesIO()
    sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def window():
    return AudioData.from_pcm16k(np.ones(8000, dtype="float32") * 0.1)


class FakeEngine:
    def __init__(self):
        self.info = EngineStatus("test-model", backend="test")
        self.calls = 0

    def status(self):
        return self.info

    def load(self):
        if self.info.state != "ready":
            self.info = replace(self.info, state="ready", load_count=1, device="test")

    def recognize(self, audio, language, context):
        self.calls += 1
        return Recognition("测试识别文字", language or "Chinese")


def finished(http, response):
    assert response.status_code == 202, response.text
    deadline = monotonic() + 5
    while True:
        state = http.get(f"/v1/transcription-jobs/{response.json()['id']}").json()
        if state["state"] in {"completed", "error", "cancelled"}:
            return state
        assert monotonic() < deadline
        threading.Event().wait(0.01)


@pytest.fixture
def client(tmp_path):
    engine = FakeEngine()
    capture = RecordingService(enumerate_devices=lambda: [{"id": "1", "kind": "system"}])
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path), engine, capture)
    ) as http:
        yield http, engine


def test_headless_api_devices_lazy_model_and_removed_web_routes(client):
    http, engine = client
    assert http.get("/").json() == {"service": "LiveCaptions ASR", "docs": "/docs"}
    assert http.get("/assets/app.js").status_code == 404
    assert http.get("/docs").status_code == 200
    assert http.get("/health").json() == {"status": "ok", "model_state": "unloaded", "ready": False}
    assert http.get("/v1/models").json()["data"][0]["supports_streaming"] is False
    assert http.get("/v1/audio/devices").json()["devices"][0]["kind"] == "system"
    assert http.post("/v1/recordings", json={}).status_code == 404
    assert http.post("/v1/audio/transcriptions").status_code == 404
    assert engine.calls == 0


def test_file_hints_and_model_reuse(client):
    http, engine = client
    for hint in ["ja", "en", "zh", "auto"]:
        state = finished(
            http,
            http.post(
                "/v1/transcription-jobs",
                files={"file": ("sample.wav", wave())},
                data={"language": hint},
            ),
        )
        assert state["state"] == "completed", state
        body = state["result"]
        assert body["text"] == "测试识别文字" and body["duration_seconds"] == 0.5
        assert body["language_mode"] == ("auto" if hint == "auto" else "hint")
    assert engine.info.load_count == 1
    assert http.get("/health").json()["ready"] is True


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"", "empty_audio"),
        (b"not audio", "invalid_audio"),
        (wave(seconds=0.02), "audio_too_short"),
    ],
)
def test_invalid_files_fail_without_model(client, payload, code):
    http, engine = client
    state = finished(
        http, http.post("/v1/transcription-jobs", files={"file": ("input.wav", payload)})
    )
    assert state["state"] == "error" and state["error"]["code"] == code
    assert engine.calls == 0


def test_bad_language_is_rejected_before_creating_job(client):
    http, engine = client
    result = http.post(
        "/v1/transcription-jobs", files={"file": ("input.wav", wave())}, data={"language": "bad"}
    )
    assert result.status_code == 422 and result.json()["error"]["code"] == "unsupported_language"
    assert engine.calls == 0


def test_silent_file_skips_model(client):
    http, engine = client
    state = finished(
        http,
        http.post("/v1/transcription-jobs", files={"file": ("silence.wav", wave(silent=True))}),
    )
    assert state["result"]["silent"] and state["result"]["text"] == ""
    assert engine.info.load_count == 0


def test_explicit_deployment_limits(tmp_path):
    config = Settings(_env_file=None, data_dir=tmp_path, max_audio_seconds=0.2, max_upload_mb=1)
    with TestClient(create_app(config, FakeEngine())) as http:
        state = finished(
            http, http.post("/v1/transcription-jobs", files={"file": ("long.wav", wave())})
        )
        assert state["error"]["code"] == "audio_too_long"
        response = http.post(
            "/v1/transcription-jobs", files={"file": ("large.wav", b"0" * (1024 * 1024 + 1))}
        )
        assert (
            response.status_code == 413 and response.json()["error"]["code"] == "upload_too_large"
        )


def test_health_stays_responsive_and_window_admission_is_bounded(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingEngine(FakeEngine):
        def recognize(self, audio, language, context):
            entered.set()
            assert release.wait(5)
            return super().recognize(audio, language, context)

    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, max_inflight_requests=1), BlockingEngine()
    )
    with TestClient(app) as http, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(app.state.service.recognize, window())
        try:
            assert entered.wait(5)
            assert http.get("/health").json()["status"] == "ok"
            with pytest.raises(ASRError) as exc:
                app.state.service.recognize(window())
            assert exc.value.code == "server_busy"
        finally:
            release.set()
        assert pending.result(timeout=5).text
