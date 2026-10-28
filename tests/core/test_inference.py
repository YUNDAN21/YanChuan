import io
from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from livecaptions_asr import Recognizer, RecognizerOptions
from livecaptions_asr.audio import AudioDecoder
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


def test_chunk_decoder_resamples_stereo_and_flac():
    decoder = AudioDecoder(None, None)
    (chunk,) = decoder.chunks(io.BytesIO(wave(sample_rate=48000, channels=2)))
    audio = chunk.audio
    assert audio.sample_rate == 16000 and audio.samples.shape == (8000,)
    assert not audio.is_silent
    output = io.BytesIO()
    sf.write(output, audio.samples, 16000, format="FLAC")
    (chunk,) = decoder.chunks(output)
    assert chunk.audio.samples.shape == (8000,)


def test_inference_failure_releases_capacity():
    class FailingEngine(FakeEngine):
        def recognize(self, audio, language, context):
            raise ASRError("out_of_memory", "test GPU OOM")

    service = Recognizer(FailingEngine(), options=RecognizerOptions(max_inflight=1))
    for _ in range(2):
        with pytest.raises(ASRError, match="test GPU OOM"):
            service.recognize(window())


def test_prepare_file_and_live_window_share_model():
    engine = FakeEngine()
    service = Recognizer(engine)
    service.prepare()
    service.prepare()
    assert engine.calls == 1
    service.recognize(window())
    service.transcribe_file(io.BytesIO(wave()))
    assert engine.calls == 3 and engine.status().load_count == 1
