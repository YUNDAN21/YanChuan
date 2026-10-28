from dataclasses import replace
from itertools import pairwise

import numpy as np
import pytest
import soundfile as sf

from livecaptions_asr import Recognizer, Segment
from livecaptions_asr.audio import AudioDecoder
from livecaptions_asr.domain import EngineStatus, Recognition
from livecaptions_asr.semantic import SemanticGrouper


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


def test_long_file_uses_bounded_windows_and_preserves_timeline(tmp_path):
    path = make_audio(tmp_path / "long.wav")
    decoder = AudioDecoder(None, None)
    chunks = list(decoder.chunks(path, 20))
    assert chunks[0].start == 0 and chunks[-1].end == 145
    assert all(left.end == right.start for left, right in pairwise(chunks))
    assert all(chunk.audio.duration_seconds <= 20.1 for chunk in chunks)
    engine = MeasuringEngine()
    result = Recognizer(engine, decoder).transcribe_file(path, "zh")
    assert result.duration_seconds == 145
    assert result.chunks_completed == 8
    assert max(engine.windows) <= 20.1 and engine.status().load_count == 1
    assert result.semantic_sections


@pytest.mark.parametrize(
    "first,second",
    [
        ("如果。", "明天下雨就取消。"),
        ("We stayed because.", "It was raining."),
        ("雨なので。", "出発を延期します。"),
    ],
)
def test_semantic_group_waits_for_complete_sentence(first, second):
    grouper = SemanticGrouper()
    assert grouper.add(Segment(0, 0, 3, first, boundary="pause")) == []
    result = grouper.add(Segment(1, 3.2, 6, second, boundary="pause"))
    assert len(result) == 1 and result[0].segment_ids == (0, 1)
    assert result[0].boundary == "sentence_and_pause"


def test_forced_recognition_windows_do_not_become_sentence_boundaries():
    grouper = SemanticGrouper(60)
    for index in range(2):
        assert (
            grouper.add(
                Segment(
                    index,
                    index * 20,
                    (index + 1) * 20,
                    "A partial sentence.",
                    boundary="max_duration",
                )
            )
            == []
        )
    result = grouper.add(Segment(2, 40, 60, "The rest.", boundary="max_duration"))
    assert result[0].boundary == "max_duration"
    assert result[0].segment_ids == (0, 1, 2)
