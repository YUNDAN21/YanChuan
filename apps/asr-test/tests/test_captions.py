import threading
from itertools import pairwise
from time import monotonic

import numpy as np
import pytest
from fastapi.testclient import TestClient
from livecaptions_audio.mixer import CapturedBlock, mix_samples
from livecaptions_testapp.api import create_app
from livecaptions_testapp.captions.service import CaptionService
from livecaptions_testapp.capture.service import RecordingService
from livecaptions_testapp.config import Settings

from livecaptions_asr import EnergySegmenter, PCMStream, RecognitionResult
from livecaptions_asr.domain import ASRError, AudioData
from livecaptions_asr.results import Timing


def wait_for(condition):
    deadline = monotonic() + 5
    while not condition():
        assert monotonic() < deadline, "Condition timed out"
        threading.Event().wait(0.01)


def test_segment_revises_same_partial_then_confirms_after_silence():
    segmenter = EnergySegmenter()
    assert segmenter.feed(np.ones(19200) * 0.1, 0) == []
    first = segmenter.partial(1.2)
    assert first.segment_id == 0 and not first.final
    segmenter.feed(np.ones(16000) * 0.1, 1.2)
    assert segmenter.partial(2.2).segment_id == first.segment_id
    finals = segmenter.feed(np.zeros(16000), 2.2)
    assert len(finals) == 1
    assert finals[0].segment_id == first.segment_id and finals[0].final
    assert segmenter.flush(3.2) is None


def test_silence_is_not_submitted_and_long_speech_is_bounded():
    segmenter = EnergySegmenter(max_seconds=3)
    assert segmenter.feed(np.zeros(32000), 0) == []
    assert segmenter.partial(2) is None
    finals = segmenter.feed(np.ones(160000) * 0.1, 2)
    assert len(finals) >= 3
    assert all(window.end - window.start <= 3.11 for window in finals)
    assert all(a.end <= b.start for a, b in pairwise(finals))
    tail = segmenter.flush(12)
    assert tail and tail.final and tail.end == 12


def test_mixer_window_crops_block_crossing_the_left_edge():
    samples = np.arange(16000, dtype=np.float32) / 16000
    actual = mix_samples([[CapturedBlock(0, 16000, samples)]], 0.25, 0.5)
    assert len(actual) == 4000
    np.testing.assert_allclose(actual, samples[8000:12000], atol=1 / 32768)


class ManualCapture:
    def __init__(self):
        self.available = 1.4
        self.state = "stopped"
        self.warnings = []
        self.opened = False
        self.samples = np.full(160000, 0.1, dtype=np.float32)
        self.archive = None

    def start(self):
        self.state = "recording"
        self.opened = True

    def status(self):
        return {"state": self.state, "duration_seconds": self.available, "limit_seconds": 10}

    def window(self, start, end):
        return AudioData.from_pcm16k(
            mix_samples([[CapturedBlock(0, 16000, self.samples)]], end - start, start)
        )

    def flush_to(self, end):
        if self.archive and not self.archive.closed:
            self.archive.append(self.samples[self.archive.frames : round(end * 16000)])

    def heartbeat(self):
        pass

    def stop(self):
        self.state = "stopped"
        self.flush_to(self.available)
        if self.archive:
            self.archive.finish()
        return self.archive


class FakeASR:
    def __init__(self):
        self.calls = 0

    def prepare(self):
        pass

    def create_stream(self, language, *, options, segmenter=None):
        return PCMStream(self, language, options=options, segmenter=segmenter)

    def recognize(self, audio, language, context):
        self.calls += 1
        return RecognitionResult(
            f"字幕版本 {self.calls}",
            "Chinese",
            "hint",
            audio.duration_seconds,
            False,
            "test",
            "test",
            "test",
            None,
            Timing(inference_seconds=0.01),
        )


def make_service(tmp_path, asr=None, *, segmenter_factory=None):
    capture = ManualCapture()

    def factory(*args, archive, **kwargs):
        capture.archive = archive
        return capture

    recordings = RecordingService(10, factory=factory, archive_dir=tmp_path / "archives")
    service = CaptionService(
        asr or FakeASR(),
        recordings,
        Settings(_env_file=None, live_segmentation="model" if segmenter_factory else "energy"),
        segmenter_factory=segmenter_factory,
    )
    return service, recordings, capture


def test_model_rejects_pause_then_confirms_same_caption_segment(tmp_path):
    from livecaptions_asr import ModelSegmenter

    class Speech:
        frame_samples = 512

        def probability(self, pcm):
            return float(np.max(np.abs(pcm)) > 0.01)

        def reset(self):
            pass

    class Endpoint:
        context_samples = 128000
        calls = 0

        def probability(self, pcm):
            self.calls += 1
            return 0.1 if self.calls == 1 else 0.9

    endpoint = Endpoint()
    service, _, capture = make_service(
        tmp_path, segmenter_factory=lambda: ModelSegmenter(Speech(), endpoint)
    )
    capture.samples = np.concatenate(
        (np.full(22400, 0.1), np.zeros(12800), np.full(22400, 0.2), np.zeros(16000))
    ).astype(np.float32)
    try:
        started = service.start("system", None, None, "zh")
        session = service.find(started["id"])
        wait_for(lambda: session.snapshot()["revision"] > 0)
        capture.available = 2.3
        wait_for(lambda: session.snapshot()["processed_seconds"] >= 2.1)
        assert endpoint.calls == 1
        assert all(not segment["final"] for segment in session.snapshot()["segments"])
        capture.available = 4.5
        wait_for(lambda: any(segment["final"] for segment in session.snapshot()["segments"]))
        result = session.snapshot()
        assert result["segmentation"] == "model"
        assert len(result["segments"]) == 1
        assert result["segments"][0]["boundary"] == "semantic_endpoint"
        assert result["segments"][0]["end_seconds"] > 3.6
        assert endpoint.calls == 2
        service.stop(session.id)
        assert session.done.wait(5)
    finally:
        service.close()


def test_model_preparation_failure_never_opens_capture(tmp_path):
    def broken():
        raise ASRError("endpoint_model_missing", "missing model")

    service, recordings, capture = make_service(tmp_path, segmenter_factory=broken)
    try:
        session = service.find(service.start("system", None, None)["id"])
        assert session.done.wait(5)
        assert session.snapshot()["error"]["code"] == "endpoint_model_missing"
        assert not capture.opened and not recordings.active()
    finally:
        service.close()


def test_partial_is_visible_before_stop_and_tail_replaces_it(tmp_path):
    service, recordings, capture = make_service(tmp_path)
    try:
        started = service.start("both", "mic", "system", "zh", save_audio=True)
        session = service.find(started["id"])
        wait_for(lambda: session.snapshot()["revision"] > 0)
        partial = session.snapshot()
        assert partial["state"] == "recording"
        assert not partial["segments"][0]["final"]
        with pytest.raises(ASRError):
            service.start("system", None, None)
        with pytest.raises(ASRError):
            recordings.start("microphone", None, None)
        capture.available = 2.05
        service.stop(session.id)
        assert session.done.wait(5)
        result = session.snapshot()
        assert result["state"] == "stopped"
        assert len(result["segments"]) == 1
        assert result["segments"][0]["final"]
        assert result["segments"][0]["end_seconds"] == 2.05
        assert result["revision"] > partial["revision"]
        assert result["audio_available"] and not recordings.active()
    finally:
        service.close()


def test_cancel_during_preparation_never_opens_device(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowASR(FakeASR):
        def prepare(self):
            entered.set()
            assert release.wait(5)

    service, recordings, capture = make_service(tmp_path, SlowASR())
    try:
        started = service.start("system", None, None)
        assert entered.wait(5)
        with pytest.raises(ASRError):
            service.start("system", None, None)
        session = service.find(started["id"])
        service.stop(session.id, cancel=True)
        release.set()
        assert session.done.wait(5)
        assert not capture.opened and not recordings.active()
    finally:
        release.set()
        service.close()


def test_captions_without_save_do_not_retain_full_audio(tmp_path):
    service, _, _ = make_service(tmp_path)
    try:
        session = service.find(service.start("system", None, None)["id"])
        assert session.ready.wait(5)
        service.stop(session.id)
        assert session.done.wait(5)
        assert not session.snapshot()["audio_available"]
        assert not session.snapshot()["save_audio"]
        assert session.audio is None and not (tmp_path / "archives").exists()
    finally:
        service.close()


def test_saved_session_reprocessing_uses_segmented_file_jobs(tmp_path):
    from types import SimpleNamespace

    from livecaptions_testapp.archive import AudioArchive

    archive = AudioArchive(tmp_path / "saved")
    archive.append(np.zeros(16000, dtype="float32"))
    archive.finish()
    done = threading.Event()
    done.set()

    class SavedCaptions:
        def find(self, session_id):
            assert session_id == "saved-session"
            return SimpleNamespace(done=done, audio=archive)

        def close(self):
            pass

    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data"), caption_service=SavedCaptions()
    )
    with TestClient(app) as http:
        submitted = http.post(
            "/v1/transcription-jobs/from-session", json={"session_id": "saved-session"}
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]
        wait_for(
            lambda: http.get(f"/v1/transcription-jobs/{job_id}").json()["state"] == "completed"
        )
        result = http.get(f"/v1/transcription-jobs/{job_id}").json()["result"]
        assert result["silent"] and result["chunks_completed"] == 1
        assert http.get("/v1/models").json()["data"][0]["load_count"] == 0


def test_capture_auto_stop_drains_and_failure_releases_capture(tmp_path):
    service, recordings, capture = make_service(tmp_path)
    try:
        session = service.find(service.start("system", None, None)["id"])
        wait_for(lambda: session.snapshot()["revision"] > 0)
        capture.state = "stopped"
        assert session.done.wait(5)
        assert session.snapshot()["segments"][-1]["final"]
        assert not recordings.active()
    finally:
        service.close()

    class BrokenASR(FakeASR):
        def recognize(self, audio, language, context):
            raise ASRError("out_of_memory", "test OOM")

    service, recordings, capture = make_service(tmp_path, BrokenASR())
    try:
        session = service.find(service.start("system", None, None)["id"])
        assert session.done.wait(5)
        assert session.snapshot()["error"]["code"] == "out_of_memory"
        assert capture.state == "stopped" and not recordings.active()
    finally:
        service.close()


def test_archive_sink_failure_does_not_leave_stream_or_device_running(tmp_path, monkeypatch):
    service, recordings, capture = make_service(tmp_path)
    capture.samples[:16000] = 0.1
    capture.samples[16000:] = 0
    capture.available = 2

    def fail_archive(*args):
        raise ASRError("storage_failed", "test disk failure")

    monkeypatch.setattr(recordings, "archive", fail_archive)
    try:
        session = service.find(service.start("system", None, None)["id"])
        wait_for(lambda: session.snapshot()["revision"] > 0)
        service.stop(session.id)
        assert session.done.wait(5)
        assert session.snapshot()["error"]["code"] == "storage_failed"
        assert not recordings.active() and capture.state == "stopped"
    finally:
        service.close()


def test_caption_http_contract(tmp_path):
    service, recordings, _ = make_service(tmp_path)
    app = create_app(
        Settings(_env_file=None), recording_service=recordings, caption_service=service
    )
    try:
        with TestClient(app) as http:
            assert http.post("/v1/captions", json={"language": "bad"}).status_code == 422
            assert (
                http.post(
                    "/v1/captions", json={}, headers={"Origin": "https://elsewhere.example"}
                ).status_code
                == 403
            )
            response = http.post(
                "/v1/captions", json={"source": "system", "language": "zh", "save_audio": True}
            )
            assert response.status_code == 200
            session_id = response.json()["id"]
            wait_for(lambda: service.find(session_id).snapshot()["revision"] > 0)
            assert http.get(f"/v1/captions/{session_id}").json()["native_streaming"] is False
            assert http.get(f"/v1/captions/{session_id}/audio").status_code == 409
            http.post(f"/v1/captions/{session_id}/stop").raise_for_status()
            assert service.find(session_id).done.wait(5)
            assert (
                http.get(f"/v1/captions/{session_id}/audio").headers["content-type"] == "audio/wav"
            )
            assert http.get("/v1/captions/missing").status_code == 404
    finally:
        service.close()
