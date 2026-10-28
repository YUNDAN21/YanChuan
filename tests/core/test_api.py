"""Library contracts exercised without HTTP, hardware or archive objects."""

import io
import json
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError, replace
from itertools import pairwise

import numpy as np
import pytest
import soundfile as sf

from livecaptions_asr import (
    ASRError,
    AudioChunk,
    AudioData,
    AudioDecoder,
    EngineStatus,
    Recognition,
    Recognizer,
    RecognizerOptions,
    Segment,
    Sentence,
    StreamOptions,
    Window,
)


class Engine:
    def __init__(self):
        self.info = EngineStatus("memory-engine", backend="test")
        self.calls = []

    def status(self):
        return self.info

    def load(self):
        if self.info.state != "ready":
            self.info = replace(self.info, state="ready", load_count=1, device="test")

    def recognize(self, audio, language, context):
        self.calls.append((audio.samples.copy(), language, context))
        return Recognition("完整句子。", language or "Chinese")


def test_core_runs_with_all_outer_imports_blocked(tmp_path):
    # Fresh interpreter: earlier HTTP tests must not hide accidental dependencies.
    code = """
import importlib.abc
import sys
class BlockOuter(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ("fastapi", "starlette", "pydantic", "pydantic_settings", "torch", "onnxruntime", "transformers",
                     "qwen_asr", "pyaudiowpatch", "livecaptions_testapp", "livecaptions_asr.api",
                     "livecaptions_asr.config", "livecaptions_asr.capture",
                     "livecaptions_asr.archive", "livecaptions_asr.jobs",
                     "livecaptions_asr.bootstrap")
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise AssertionError("Outer dependency imported: " + fullname)
sys.meta_path.insert(0, BlockOuter())
from livecaptions_asr import AudioData, EngineStatus, Recognition, Recognizer
from livecaptions_asr.engines.native_qwen import NativeQwenEngine
from livecaptions_asr.engines.native_options import NativeQwenOptions
from pathlib import Path
import numpy as np
class Engine:
    def load(self): pass
    def status(self): return EngineStatus("test", state="ready")
    def recognize(self, audio, language, context): return Recognition("一句话。", "Chinese")
recognizer = Recognizer(Engine())
assert recognizer.recognize(AudioData.from_pcm16k(np.ones(16000)*0.1)).text
stream = recognizer.create_stream("zh")
events = list(stream.feed(np.ones(20000)*0.1)) + list(stream.finish())
assert {event.kind for event in events} == {"partial", "final", "sentence"}
assert NativeQwenEngine(NativeQwenOptions(Path("model.gguf"), Path("crispasr.dll"))).status().state == "unloaded"
assert len(__import__("threading").enumerate()) == 1
print("isolated core passed")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "isolated core passed" in result.stdout
    assert list(tmp_path.iterdir()) == []


def collect(pcm, packet_size):
    engine = Engine()
    stream = Recognizer(engine).create_stream(
        "zh", "专业词", options=StreamOptions(max_segment_seconds=3)
    )
    events = []
    for offset in range(0, len(pcm), packet_size):
        events.extend(stream.feed(pcm[offset : offset + packet_size]))
        assert stream.buffered_seconds <= 3.3
    events.extend(stream.finish())
    assert stream.buffered_seconds == 0
    return events, engine, stream


@pytest.mark.parametrize("packet_size", [17, 1599, 1600, 7493, 16000, 150000])
def test_arbitrary_pcm_packets_preserve_windows_and_exact_tail(packet_size):
    pcm = np.concatenate((np.zeros(3200), np.full(120811, 0.1), np.zeros(17000))).astype("float32")
    events, engine, stream = collect(pcm, packet_size)
    expected, _, _ = collect(pcm, 1600)

    def stable(event):
        value = event.to_dict()
        if value["segment"]:
            value["segment"].pop("inference_seconds")
        return value

    assert list(map(stable, events)) == list(map(stable, expected))
    finals = [event.segment for event in events if event.kind == "final"]
    assert len(finals) >= 3 and all(segment.final for segment in finals)
    assert all(left.end_seconds <= right.start_seconds for left, right in pairwise(finals))
    assert stream.duration_seconds == len(pcm) / 16000
    assert all(len(samples) <= 3.11 * 16000 for samples, _, _ in engine.calls)
    assert all(
        language == "Chinese" and context == "专业词" for _, language, context in engine.calls
    )
    assert list(stream.finish()) == []


def test_partial_revision_final_tail_and_sentence_are_separate_events():
    engine = Engine()
    stream = Recognizer(engine).create_stream()
    first = list(stream.feed(np.full(19200, 0.1)))
    assert [event.kind for event in first] == ["partial"]
    tail = list(stream.feed(np.full(13811, 0.1))) + list(stream.finish())
    final = next(event.segment for event in tail if event.kind == "final")
    assert final.id == first[0].segment.id and final.revision > first[0].segment.revision
    assert final.end_seconds == pytest.approx(33011 / 16000, abs=0.0001)
    assert tail[-1].kind == "sentence" and tail[-1].sentence.segment_ids == (final.id,)
    with pytest.raises(FrozenInstanceError):
        final.text = "mutate"
    assert json.loads(json.dumps(tail[-1].to_dict()))["sentence"]["segment_ids"] == [0]


def test_shared_model_independent_stream_state_cancel_and_silence():
    engine = Engine()
    recognizer = Recognizer(engine)
    left, right = recognizer.create_stream("zh"), recognizer.create_stream("en")
    assert list(left.feed(np.ones(17000) * 0.1))
    assert list(right.feed(np.ones(34000) * 0.2))
    left.cancel()
    assert left.closed and left.buffered_seconds == 0
    assert list(left.finish()) == []
    with pytest.raises(ASRError, match="已经结束"):
        list(left.feed(np.zeros(16000)))
    result = list(right.finish())
    assert result[-1].kind == "sentence"
    assert next(event.segment for event in result if event.kind == "final").end_seconds == 2.125
    silent = recognizer.create_stream()
    assert list(silent.feed(np.zeros(32000))) + list(silent.finish()) == []
    assert engine.status().load_count == 1
    assert {language for _, language, _ in engine.calls} == {"Chinese", "English"}


def test_long_pcm_is_bounded_and_does_not_retain_input_array():
    # Store only scalar measurements in this engine; fake calls must not grow memory.
    class SmallEngine(Engine):
        def recognize(self, audio, language, context):
            return Recognition("一句话。", "Chinese")

    stream = Recognizer(SmallEngine()).create_stream()
    samples = np.full(16000, 0.1, dtype="float32")
    finals = 0
    for _ in range(180):
        finals += sum(event.kind == "final" for event in stream.feed(samples, emit_partial=False))
        assert stream.buffered_seconds <= 8.3
    assert finals >= 20 and stream.duration_seconds == 180
    list(stream.finish())
    assert stream.buffered_seconds == 0


def test_invalid_input_and_reentrant_consumption_have_explicit_errors():
    for seconds in [0, -1, 0.000001, float("nan"), float("inf")]:
        with pytest.raises(ASRError):
            RecognizerOptions(chunk_seconds=seconds)
        with pytest.raises(ASRError):
            next(AudioDecoder().chunks("not-opened.wav", seconds))
    for capacity in [0, -1, 0.5, True, float("nan")]:
        with pytest.raises(ASRError):
            RecognizerOptions(max_inflight=capacity)
    stream = Recognizer(Engine()).create_stream()
    for invalid in [np.zeros((2, 100)), np.array([np.nan]), np.array([np.inf])]:
        with pytest.raises(ASRError):
            list(stream.feed(invalid))
    assert stream.duration_seconds == 0
    pending = stream.feed(np.ones(16000) * 0.1)
    assert next(pending).kind == "partial"
    with pytest.raises(ASRError) as error:
        list(stream.feed(np.zeros(100)))
    assert error.value.code == "stream_busy"
    list(pending)
    list(stream.finish())


def test_model_error_closes_only_affected_stream_and_releases_capacity():
    class Failing(Engine):
        def recognize(self, audio, language, context):
            raise ASRError("inference_failed", "model failed")

    recognizer = Recognizer(Failing(), options=RecognizerOptions(max_inflight=1))
    for _ in range(2):
        stream = recognizer.create_stream()
        with pytest.raises(ASRError, match="model failed"):
            list(stream.feed(np.ones(16000) * 0.1))
        assert stream.closed and stream.buffered_seconds == 0


def test_file_api_progress_cancel_and_caller_owned_handle():
    source = io.BytesIO()
    sf.write(source, np.ones(65000) * 0.1, 16000, format="WAV")
    engine = Engine()
    recognizer = Recognizer(engine, options=RecognizerOptions(chunk_seconds=1))
    updates = []
    result = recognizer.transcribe_file(source, "zh", on_segment=updates.append)
    assert not source.closed
    assert result.chunks_completed == len(updates) > 1
    assert isinstance(updates[0].segment, Segment)
    assert result.duration_seconds == pytest.approx(65000 / 16000, abs=0.0001)
    cancel = threading.Event()

    def stop_after_first(progress):
        cancel.set()

    count = len(engine.calls)
    with pytest.raises(ASRError) as error:
        recognizer.transcribe_file(source, on_segment=stop_after_first, cancel=cancel)
    assert error.value.code == "cancelled" and len(engine.calls) == count + 1
    assert not source.closed
    assert recognizer.recognize(AudioData.from_pcm16k(np.zeros(16000))).silent


def test_segmentation_grouping_and_file_decoder_are_replaceable():
    class Segmenter:
        start = None

        def feed(self, samples, offset):
            return [Window(99, offset, offset + len(samples) / 16000, True, "custom")]

        def partial(self, end):
            return None

        def flush(self, end):
            return None

    class Grouper:
        def add(self, segment):
            return [
                Sentence(
                    7,
                    segment.start_seconds,
                    segment.end_seconds,
                    "自定义组合",
                    (segment.id,),
                    "custom",
                )
            ]

        def flush(self):
            return []

    class Decoder:
        def chunks(self, source, chunk_seconds):
            # Does not open a path; a future decoder can read a different source.
            assert source == "not-a-real-file"
            yield AudioChunk(0, 1, AudioData.from_pcm16k(np.ones(16000) * 0.1), "end")

    recognizer = Recognizer(Engine(), decoder=Decoder())
    stream = recognizer.create_stream(segmenter=Segmenter(), grouper=Grouper())
    events = list(stream.feed(np.ones(1600) * 0.1)) + list(stream.finish())
    assert [event.kind for event in events] == ["final", "sentence"]
    assert events[0].segment.id == 99
    assert events[1].sentence.text == "自定义组合"
    result = recognizer.transcribe_file("not-a-real-file", grouper=Grouper())
    assert result.chunks_completed == 1 and result.semantic_sections[0].text == "自定义组合"


def test_closing_unconsumed_event_iterator_cancels_stream():
    stream = Recognizer(Engine()).create_stream()
    pending = stream.feed(np.ones(32000) * 0.1)
    assert next(pending).kind == "partial"
    pending.close()
    assert stream.closed and stream.buffered_seconds == 0
