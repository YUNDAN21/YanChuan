"""Native adapter contracts independent of the DLL, GPU and UI."""

import struct
import threading
from pathlib import Path

import numpy as np
import pytest

from livecaptions_asr import ASRError, AudioData
from livecaptions_asr.engines import native_qwen
from livecaptions_asr.engines.gguf_model import inspect_qwen_gguf
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine


class Binding:
    def __init__(self, path):
        self.opens = self.closes = 0
        self.requests = []

    def open(self, options):
        self.opens += 1
        return 123, "Vulkan1", "Discrete GPU"

    def recognize(self, handle, samples, language, context):
        self.requests.append((handle, samples.copy(), language, context))
        return "识别文字", "Chinese"

    def close(self, handle):
        assert handle == 123
        self.closes += 1

    def warmup(self, handle, samples, max_tokens):
        assert handle == 123 and len(samples) == 16000 and max_tokens == 512


@pytest.fixture
def engine(tmp_path, monkeypatch):
    model = tmp_path / "测试模型.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(native_qwen, "NativeBinding", Binding)
    monkeypatch.setattr(
        native_qwen, "inspect_qwen_gguf", lambda path: {"precision": "q8_0", "model_size": "0.6B"}
    )
    return NativeQwenEngine(NativeQwenOptions(model, tmp_path / "crispasr.dll"))


def test_loading_is_serialized_reused_and_explicitly_released(engine):
    threads = [threading.Thread(target=engine.load) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    binding = engine._binding
    assert binding.opens == engine.status().load_count == 1
    assert engine.status().device == "Vulkan1"
    engine.close()
    engine.close()
    assert binding.closes == 1
    engine.load()
    assert binding.opens == 2
    assert engine.status().load_count == 2
    engine.close()


def test_pcm_context_and_language_are_forwarded_without_changes(engine):
    engine.load()
    samples = np.full(16000, 0.1, dtype=np.float32)
    result = engine.recognize(AudioData.from_pcm16k(samples), "Chinese", "倭瓜；第 10 波")
    _, received, language, context = engine._binding.requests[-1]
    np.testing.assert_array_equal(received, samples)
    assert language == "zh" and context == "倭瓜；第 10 波"
    assert result.text == "识别文字" and result.language == "Chinese"
    engine.recognize(AudioData.from_pcm16k(samples), None, "")
    assert engine._binding.requests[-1][2:] == (None, "")
    engine.close()


def test_silence_does_not_hallucinate(engine):
    engine.load()
    assert engine.recognize(AudioData.from_pcm16k(np.zeros(16000)), None, "").text == ""
    assert not engine._binding.requests
    engine.close()


def test_failed_load_does_not_leave_a_ready_model(engine, monkeypatch):
    def failed(self, options):
        raise ASRError("out_of_memory", "GPU 显存不足")

    monkeypatch.setattr(Binding, "open", failed)
    with pytest.raises(ASRError, match="显存"):
        engine.load()
    assert engine.status().state == "error" and engine.status().load_count == 0
    engine.close()
    with pytest.raises(ASRError, match="尚未加载"):
        engine.recognize(AudioData.from_pcm16k(np.ones(16000)), None, "")


@pytest.mark.parametrize("device", ["cuda", "cuda:0", "Vulkan-1", "", "auto"])
def test_device_configuration_is_explicit(device):
    with pytest.raises(ASRError):
        NativeQwenOptions(Path("model"), Path("library"), device=device)


@pytest.mark.parametrize(
    "payload", [b"", b"GGUF", b"not a model", b"GGUF\x03\x00\x00\x00" + b"\xff" * 16]
)
def test_bad_models_are_rejected_before_loading_native_code(tmp_path, payload):
    path = tmp_path / "bad.gguf"
    path.write_bytes(payload)
    with pytest.raises(ASRError) as error:
        inspect_qwen_gguf(path)
    assert error.value.code == "invalid_model"


def test_gguf_checks_layout_precision_and_weight_extent(tmp_path):
    def string(value):
        data = value.encode()
        return struct.pack("<Q", len(data)) + data

    data = b"GGUF" + struct.pack("<IQQ", 3, 3, 3)
    data += string("general.architecture") + struct.pack("<I", 8) + string("qwen3asr")
    data += string("general.file_type") + struct.pack("<II", 4, 7)
    data += string("qwen3asr.llm.d_model") + struct.pack("<II", 4, 1024)
    for index, name in enumerate(("audio.mel_filters", "audio.mel_window", "token_embd.weight")):
        data += string(name) + struct.pack("<IQIQ", 1, 32, 8, index * 64)
    data += b"\0" * (-len(data) % 32) + b"\0" * 162
    path = tmp_path / "test.gguf"
    path.write_bytes(data)
    assert inspect_qwen_gguf(path)["precision"] == "q8_0"
    path.write_bytes(data[:-1])
    with pytest.raises(ASRError, match="truncated weights"):
        inspect_qwen_gguf(path)


def test_recognizer_close_releases_engine_and_resets_warmup(engine):
    from livecaptions_asr import Recognizer

    recognizer = Recognizer(engine)
    recognizer.prepare()
    recognizer.close()
    assert not recognizer._warmed and engine.status().state == "unloaded"
    recognizer.prepare()
    assert engine.status().load_count == 2
    recognizer.close()
