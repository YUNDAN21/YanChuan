"""Native Qwen model adapter; owns no segmentation, configuration or transport."""

import threading
from dataclasses import replace
from time import perf_counter

import numpy as np

from ..domain import LANGUAGES, ASRError, EngineStatus, Recognition
from ..startup import StartupTimer
from .gguf_model import inspect_qwen_gguf
from .native_binding import NativeBinding
from .native_options import NativeQwenOptions


class NativeQwenEngine:
    def __init__(self, options: NativeQwenOptions, *, on_progress=None):
        self.settings = options
        self._lock = threading.RLock()
        self._status_lock = threading.Lock()
        self._binding = self._handle = None
        self._status = EngineStatus(model_id=options.model_id, backend="qwen3-ggml-vulkan")
        self._startup = StartupTimer(on_progress)

    def status(self):
        with self._status_lock:
            return self._status

    def _update(self, **changes):
        with self._status_lock:
            self._status = replace(self._status, **changes)

    def load(self):
        with self._lock:
            if self._handle:
                self._startup.emit("native_model", "reused")
                return
            self._update(state="loading", last_error=None)
            started = perf_counter()
            try:
                if not self.settings.model_path.is_file():
                    raise ASRError("model_missing", "找不到 GGUF 模型。")
                with self._startup.phase("native_model_validate"):
                    metadata = inspect_qwen_gguf(self.settings.model_path)
                    if metadata["precision"] != self.settings.precision:
                        raise ASRError("invalid_model", "模型精度与配置不匹配。")
                    if not self.settings.model_id.endswith(metadata["model_size"]):
                        raise ASRError("invalid_model", "模型参数规模与选择的型号不匹配。")
                with self._startup.phase("native_library"):
                    if self._binding is None:
                        self._binding = NativeBinding(self.settings.library_path)
                with self._startup.phase("native_weights"):
                    handle, device, description = self._binding.open(self.settings)
                self._handle = handle
                self._update(
                    state="ready",
                    device=device,
                    device_name=description,
                    runtime_version="CrispASR 0.8.45 + LiveCaptions ABI 1",
                    dtype=self.settings.precision,
                    load_count=self.status().load_count + 1,
                    load_seconds=round(perf_counter() - started, 4),
                )
            except Exception as exc:
                self._update(state="error", last_error=str(exc))
                raise

    def recognize(self, audio, language, context):
        with self._lock:
            if not self._handle:
                raise ASRError("model_not_ready", "模型尚未加载。")
            if audio.is_silent:
                return Recognition("", "unknown")
            hint = next((key for key, value in LANGUAGES.items() if value == language), language)
            text, detected = self._binding.recognize(self._handle, audio.samples, hint, context)
            return Recognition(text, LANGUAGES.get(detected, detected) or language or "unknown")

    def close(self):
        with self._lock:
            if self._handle:
                self._binding.close(self._handle)
                self._handle = None
            self._update(state="unloaded")

    def warmup(self):
        with self._lock:
            if not self._handle:
                raise ASRError("model_not_ready", "模型尚未加载。")
            self._binding.warmup(
                self._handle, np.zeros(16000, dtype=np.float32), self.settings.max_new_tokens
            )

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, *args):
        self.close()
