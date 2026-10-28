"""Exercise ownership and device guarantees at the ctypes boundary without a GPU."""

import ctypes
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from livecaptions_asr import ASRError
from livecaptions_asr.engines.native_binding import NativeBinding, OpenParams
from livecaptions_asr.engines.native_options import NativeQwenOptions


def binding(**overrides):
    calls = []
    lib = SimpleNamespace(
        crispasr_set_gpu_backend=lambda name: calls.append(("device", name)),
        crispasr_session_open_with_params=lambda *args: 21,
        crispasr_session_close=lambda handle: calls.append(("close", handle)),
        lc_qwen_device=lambda handle: b"CPU",
        lc_qwen_set_greedy=lambda handle, maximum: calls.append(("tokens", maximum)) or 0,
        lc_qwen_set_context=lambda handle, context: 0,
        lc_qwen_language=lambda handle: b"Japanese",
        crispasr_session_transcribe_lang=lambda *args: 34,
        crispasr_session_result_n_segments=lambda result: 1,
        crispasr_session_result_segment_text=lambda result, index: "日本語".encode(),
        crispasr_session_result_free=lambda result: calls.append(("free", result)),
    )
    for name, value in overrides.items():
        setattr(lib, name, value)
    adapter = object.__new__(NativeBinding)
    adapter.lib = lib
    return adapter, calls


def test_open_layout_and_failed_actual_device_check_release_owned_session():
    assert ctypes.sizeof(OpenParams) == 48
    options = NativeQwenOptions(Path("模型.gguf"), Path("native.dll"), device="cpu")
    adapter, calls = binding(lc_qwen_device=lambda handle: b"Vulkan0")
    with pytest.raises(ASRError) as error:
        adapter.open(options)
    assert error.value.code == "gpu_fallback_rejected"
    assert calls == [("device", b"cpu"), ("close", 21)]


def test_result_is_freed_on_success_and_invalid_utf8():
    samples = np.ones(16000, dtype=np.float32)
    adapter, calls = binding()
    assert adapter.recognize(21, samples, None, "") == ("日本語", "Japanese")
    assert calls == [("free", 34)]
    adapter, calls = binding(crispasr_session_result_segment_text=lambda *args: b"\xff")
    with pytest.raises(UnicodeDecodeError):
        adapter.recognize(21, samples, None, "")
    assert calls == [("free", 34)]


def test_null_result_reports_inference_error_without_freeing_null():
    adapter, calls = binding(crispasr_session_transcribe_lang=lambda *args: None)
    with pytest.raises(ASRError) as error:
        adapter.recognize(21, np.zeros(16000, dtype=np.float32), "zh", "")
    assert error.value.code == "inference_failed"
    assert not calls


def test_bounded_warmup_restores_generation_limit_even_after_failure():
    adapter, calls = binding(crispasr_session_transcribe_lang=lambda *args: None)
    with pytest.raises(ASRError):
        adapter.warmup(21, np.zeros(16000, dtype=np.float32), 83)
    assert calls == [("tokens", 1), ("tokens", 83)]


def test_missing_dll_is_an_actionable_api_error(tmp_path):
    with pytest.raises(ASRError) as error:
        NativeBinding(tmp_path / "missing.dll")
    assert error.value.code == "native_library_missing"
