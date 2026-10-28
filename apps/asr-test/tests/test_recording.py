from typing import ClassVar

import numpy as np
import pytest
from livecaptions_audio.mixer import CapturedBlock, mix_samples
from livecaptions_testapp.capture.service import RecordingService

from livecaptions_asr.domain import ASRError, AudioData


def test_mix_aligns_resamples_and_preserves_silent_gaps():
    block = CapturedBlock(0.25, 48000, np.full(12000, 0.4, dtype=np.float32))
    samples = mix_samples([[block]], 1)
    assert len(samples) == 16000
    assert np.all(samples[:3900] == 0)
    assert np.mean(samples[4100:7900]) == pytest.approx(0.4, abs=0.001)
    assert np.all(samples[8100:] == 0)


def test_mix_limits_gain_and_crops_to_duration():
    block = CapturedBlock(0, 16000, np.ones(16000, dtype=np.float32))
    samples = mix_samples([[block], [block]], 0.5)
    assert len(samples) == 8000 and samples.min() > 0.99 and samples.max() <= 1
    assert np.all(mix_samples([[], []], 0.3) == 0)


class FakeRecording:
    created: ClassVar[list] = []

    def __init__(self, source, microphone_id, system_id, limit, *, archive, **options):
        self.config = (source, microphone_id, system_id, limit)
        self.archive, self.options = archive, options
        self.warnings = ["测试警告"]
        self.state = "stopped"
        self.closed = False
        self.created.append(self)

    def start(self):
        self.state = "recording"

    def status(self):
        return {"state": self.state, "duration_seconds": 0.5, "limit_seconds": self.config[3]}

    def window(self, start, end):
        return AudioData.from_pcm16k(np.zeros(round((end - start) * 16000)))

    def flush_to(self, end):
        if self.archive and not self.archive.closed:
            self.archive.append(np.zeros(max(0, round(end * 16000) - self.archive.frames)))

    def heartbeat(self):
        pass

    def stop(self):
        self.state, self.closed = "stopped", True
        self.flush_to(0.5)
        if self.archive:
            self.archive.finish()
        return self.archive


@pytest.fixture
def service(tmp_path):
    value = RecordingService(10, factory=FakeRecording, archive_dir=tmp_path / "archives")
    yield value
    value.close()


@pytest.mark.parametrize("source", ["microphone", "system", "both"])
def test_capture_selection_exclusivity_and_archive_release(service, source):
    session = service.start(source, "mic-id", "system-id", save_audio=True)
    fake = FakeRecording.created[-1]
    assert fake.config == (source, "mic-id", "system-id", 10)
    assert fake.options["buffer_seconds"] == service.buffer_seconds
    with pytest.raises(ASRError):
        service.start(source, None, None)
    service.finish(session["id"])
    assert service.window(session["id"], 0, 0.5).samples.shape == (8000,)
    archive, warnings = service.stop(session["id"])
    assert archive.closed and archive.frames == 8000 and warnings == ["测试警告"]
    assert fake.closed and not service.active()


def test_failed_capture_can_retry_without_compatibility_adapter(tmp_path):
    class BrokenRecording(FakeRecording):
        def start(self):
            raise ASRError("capture_unavailable", "device busy")

    service = RecordingService(factory=BrokenRecording, archive_dir=tmp_path / "archives")
    with pytest.raises(ASRError):
        service.start("system", None, None, save_audio=True)
    assert not service.active()
    service.factory = FakeRecording
    session = service.start("system", None, None)
    assert service.stop(session["id"])[0] is None


def test_close_releases_capture(service):
    service.start("system", None, None)
    capture = FakeRecording.created[-1]
    service.close()
    assert capture.closed and not service.active()
