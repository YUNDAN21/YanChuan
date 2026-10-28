"""Core capability acceptance: no test application, HTTP or frontend imports."""

import io
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import livecaptions_asr
from livecaptions_asr import (
    ASRError,
    AudioChunk,
    AudioData,
    AudioDecoder,
    EngineStatus,
    Recognition,
    Recognizer,
    RecognizerOptions,
    StreamOptions,
)


class Engine:
    def __init__(self):
        self.info = EngineStatus("contract-engine", backend="test")
        self.calls = []

    def status(self):
        return self.info

    def load(self):
        if self.info.state != "ready":
            self.info = replace(self.info, state="ready", load_count=1)

    def recognize(self, audio, language, context):
        self.calls.append((language, context))
        return Recognition("这是识别结果。", language or "Chinese")


def wav(seconds=1, **kwargs):
    output = io.BytesIO()
    sf.write(output, np.ones(round(16000 * seconds)) * 0.1, 16000, format="WAV", **kwargs)
    return output


def test_distribution_directory_contains_only_core_modules():
    root = Path(livecaptions_asr.__file__).parent
    outer = {
        "api.py",
        "config.py",
        "bootstrap.py",
        "__main__.py",
        "caption_api.py",
        "job_api.py",
        "jobs.py",
        "http_common.py",
        "archive.py",
        "archive_response.py",
        "static",
        "capture",
        "captions",
    }
    assert not outer.intersection(path.name for path in root.iterdir())
    assert (root / "py.typed").is_file()


@pytest.mark.parametrize(
    "hint,expected", [("zh", "Chinese"), ("ja", "Japanese"), ("en", "English"), ("auto", None)]
)
def test_language_and_context_contract_across_all_three_apis(hint, expected):
    engine = Engine()
    api = Recognizer(engine)
    audio = AudioData.from_pcm16k(np.ones(17000) * 0.1)
    window = api.recognize(audio, hint, "术语")
    file = api.transcribe_file(wav(), hint, "术语")
    stream = api.create_stream(hint, "术语")
    events = list(stream.feed(audio.samples)) + list(stream.finish())
    assert window.text and file.text and any(event.kind == "sentence" for event in events)
    assert all(call == (expected, "术语") for call in engine.calls)
    assert window.language_mode == ("auto" if expected is None else "hint")
    assert api.status().load_count == 1


def test_bad_language_never_loads_model_or_reads_file():
    engine = Engine()
    api = Recognizer(engine)
    actions = [
        lambda: api.recognize(AudioData.from_pcm16k(np.ones(16000)), "bad"),
        lambda: api.transcribe_file("does-not-exist", "bad"),
        lambda: api.create_stream("bad"),
    ]
    for action in actions:
        with pytest.raises(ASRError) as error:
            action()
        assert error.value.code == "unsupported_language"
    assert engine.info.state == "unloaded" and not engine.calls


@pytest.mark.parametrize(
    "payload,code", [(b"", "empty_audio"), (b"invalid", "invalid_audio"), (None, "audio_too_short")]
)
def test_invalid_file_contract_before_model_loading(payload, code):
    engine = Engine()
    source = wav(0.02) if payload is None else io.BytesIO(payload)
    with pytest.raises(ASRError) as error:
        Recognizer(engine).transcribe_file(source)
    assert error.value.code == code and engine.info.state == "unloaded"
    assert not source.closed


def test_file_limits_silent_input_and_local_path(tmp_path):
    source = wav()
    for decoder, expected in [
        (AudioDecoder(max_bytes=10), "upload_too_large"),
        (AudioDecoder(max_seconds=0.5), "audio_too_long"),
    ]:
        with pytest.raises(ASRError) as error:
            Recognizer(Engine(), decoder).transcribe_file(source)
        assert error.value.code == expected
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(16000), 16000)
    engine = Engine()
    result = Recognizer(engine).transcribe_file(str(path))
    assert result.silent and not result.text and not engine.calls
    assert engine.info.state == "unloaded"


@pytest.mark.parametrize(
    "samples", [[], [[0.1, 0.2]], [float("nan")], [float("inf")], ["bad"], [1j]]
)
def test_invalid_pcm_has_consistent_domain_errors(samples):
    with pytest.raises(ASRError) as error:
        AudioData.from_pcm16k(samples)
    assert error.value.code == "invalid_audio"
    stream = Recognizer(Engine()).create_stream()
    if samples == []:
        assert list(stream.feed(samples)) == []
    else:
        with pytest.raises(ASRError) as error:
            list(stream.feed(samples))
        assert error.value.code == "invalid_audio"
    assert stream.duration_seconds == 0 and not stream.closed


def test_metadata_and_options_cannot_silently_corrupt_audio():
    for duration in [0, -1, float("nan"), float("inf"), 2]:
        with pytest.raises(ASRError):
            AudioData.from_pcm16k(np.ones(16000), duration)
    with pytest.raises(ASRError):
        AudioData(np.ones(16000), 48000, 1, False)
    with pytest.raises(ASRError):
        AudioData(np.ones(16000), 16000, 1, True)
    with pytest.raises(ASRError):
        StreamOptions(max_segment_seconds=0.01)


def test_file_callback_failure_closes_decoder_and_releases_admission():
    closed = threading.Event()

    class Decoder:
        def chunks(self, source, chunk_seconds):
            try:
                yield AudioChunk(0, 1, AudioData.from_pcm16k(np.ones(16000) * 0.1), "end")
            finally:
                closed.set()

    def broken_callback(progress):
        raise RuntimeError("callback failed")

    api = Recognizer(Engine(), Decoder(), RecognizerOptions(max_inflight=1))
    with pytest.raises(RuntimeError, match="callback failed"):
        api.transcribe_file("unused", on_segment=broken_callback)
    assert closed.is_set()
    assert api.recognize(AudioData.from_pcm16k(np.zeros(16000))).silent


def test_capacity_and_cancel_while_waiting_for_model():
    entered, release, decoding, closed = (threading.Event() for _ in range(4))

    class BlockingEngine(Engine):
        def recognize(self, audio, language, context):
            entered.set()
            assert release.wait(5)
            return super().recognize(audio, language, context)

    class Decoder:
        def chunks(self, source, chunk_seconds):
            try:
                decoding.set()
                yield AudioChunk(0, 1, AudioData.from_pcm16k(np.ones(16000) * 0.1), "end")
            finally:
                closed.set()

    engine = BlockingEngine()
    api = Recognizer(engine, Decoder())
    audio = AudioData.from_pcm16k(np.ones(16000) * 0.1)
    cancel = threading.Event()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(api.recognize, audio)
        try:
            assert entered.wait(5)
            second = pool.submit(api.transcribe_file, "unused", cancel=cancel)
            assert decoding.wait(5)
            with pytest.raises(ASRError) as error:
                api.recognize(audio)
            assert error.value.code == "server_busy"
            cancel.set()
        finally:
            release.set()
        assert first.result(5).text
        with pytest.raises(ASRError) as error:
            second.result(5)
        assert error.value.code == "cancelled" and closed.is_set()
    assert len(engine.calls) == 1


def test_no_partial_mode_and_empty_finish_still_finalize_every_sentence():
    api = Recognizer(Engine())
    empty = api.create_stream()
    assert list(empty.finish()) == [] and empty.closed
    stream = api.create_stream()
    events = list(stream.feed(np.ones(9 * 16000) * 0.1, emit_partial=False))
    events.extend(stream.finish())
    assert all(event.kind != "partial" for event in events)
    assert sum(event.kind == "final" for event in events) == 2
    assert any(event.kind == "sentence" for event in events)
    assert stream.buffered_seconds == 0 and stream.duration_seconds == 9
