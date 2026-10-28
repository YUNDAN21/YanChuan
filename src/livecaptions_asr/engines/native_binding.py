"""Pinned CrispASR 0.8.45 + LiveCaptions ABI 1 binding. No model policy or pipeline."""

import ctypes as C
import os
import threading
from pathlib import Path

from ..domain import ASRError

_OPEN_LOCK = threading.Lock()  # upstream GPU preference is process-global


class OpenParams(C.Structure):
    _fields_ = [
        (name, C.c_int)
        for name in (
            "abi_version",
            "n_threads",
            "use_gpu",
            "verbosity",
            "flash_attn",
            "n_gpu_layers",
        )
    ] + [("reserved", C.c_int * 6)]


class NativeBinding:
    def __init__(self, path: Path):
        self._directory = None
        if not path.is_file():
            raise ASRError("native_library_missing", "找不到原生识别运行库。")
        try:
            if os.name == "nt":
                self._directory = os.add_dll_directory(str(path.resolve().parent))
            self.lib = C.CDLL(str(path.resolve()))
            self.base = C.CDLL(str(path.resolve().with_name("ggml-base.dll")))
            self.registry = C.CDLL(str(path.resolve().with_name("ggml.dll")))
            self.vulkan = None
            self._setup()
            if (
                self.lib.lc_qwen_abi_version() != 1
                or self.lib.lc_qwen_open_params_size() != C.sizeof(OpenParams)
                or self.lib.whisper_version().decode() != "0.8.45"
            ):
                raise ASRError("native_abi_mismatch", "原生运行库版本或 ABI 不匹配。")
        except (OSError, AttributeError) as exc:
            raise ASRError("native_library_invalid", "原生运行库或必要 DLL 缺失／不兼容。") from exc

    def _setup(self):
        p, i, s = C.c_void_p, C.c_int, C.c_char_p
        signatures = {
            "lc_qwen_abi_version": (i, []),
            "lc_qwen_open_params_size": (i, []),
            "lc_qwen_set_greedy": (i, [p, i]),
            "whisper_version": (s, []),
            "crispasr_set_gpu_backend": (None, [s]),
            "crispasr_session_open_with_params": (p, [s, s, C.POINTER(OpenParams)]),
            "crispasr_session_close": (None, [p]),
            "crispasr_session_set_max_new_tokens": (i, [p, i]),
            "crispasr_session_set_temperature": (i, [p, C.c_float, C.c_uint64]),
            "crispasr_session_set_beam_size": (i, [p, i]),
            "crispasr_session_set_best_of": (i, [p, i]),
            "lc_qwen_set_context": (i, [p, s]),
            "lc_qwen_device": (s, [p]),
            "lc_qwen_language": (s, [p]),
            "crispasr_session_transcribe_lang": (p, [p, C.POINTER(C.c_float), i, s]),
            "crispasr_session_result_n_segments": (i, [p]),
            "crispasr_session_result_segment_text": (s, [p, i]),
            "crispasr_session_result_free": (None, [p]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, args
        for name, result, args in (
            ("ggml_backend_register", None, [p]),
            ("ggml_backend_dev_count", C.c_size_t, []),
            ("ggml_backend_dev_get", p, [C.c_size_t]),
            ("ggml_backend_dev_name", s, [p]),
            ("ggml_backend_dev_description", s, [p]),
            ("ggml_backend_dev_type", i, [p]),
        ):
            owner = (
                self.registry
                if name
                in {"ggml_backend_register", "ggml_backend_dev_count", "ggml_backend_dev_get"}
                else self.base
            )
            function = getattr(owner, name)
            function.restype, function.argtypes = result, args

    def open(self, options):
        with _OPEN_LOCK:
            selected, description = "CPU", "CPU"
            if options.device != "cpu":
                try:
                    self.vulkan = C.CDLL(
                        str(options.library_path.resolve().with_name("ggml-vulkan.dll"))
                    )
                    self.vulkan.ggml_backend_vk_reg.restype = C.c_void_p
                    self.vulkan.ggml_backend_vk_reg.argtypes = []
                    count = self.registry.ggml_backend_dev_count()
                    if not any(
                        self.base.ggml_backend_dev_name(self.registry.ggml_backend_dev_get(i))
                        .decode()
                        .startswith("Vulkan")
                        for i in range(count)
                    ):
                        self.registry.ggml_backend_register(self.vulkan.ggml_backend_vk_reg())
                except (OSError, AttributeError) as exc:
                    raise ASRError(
                        "vulkan_unavailable", "Vulkan 后端加载失败，请检查运行库和显卡驱动。"
                    ) from exc
                devices = []
                for index in range(self.registry.ggml_backend_dev_count()):
                    device = self.registry.ggml_backend_dev_get(index)
                    name = self.base.ggml_backend_dev_name(device).decode()
                    if name.startswith("Vulkan"):
                        devices.append(
                            (
                                self.base.ggml_backend_dev_type(device),
                                name,
                                self.base.ggml_backend_dev_description(device).decode(),
                            )
                        )
                # ggml GPU=1, IGPU=2. Prefer a discrete GPU; explicit names select exactly.
                candidates = [
                    d for d in devices if options.device == "vulkan" or d[1] == options.device
                ]
                if not candidates:
                    raise ASRError("vulkan_unavailable", "没有可用的指定 Vulkan GPU。")
                _, selected, description = min(candidates, key=lambda d: (d[0] != 1, d[1]))
            self.lib.crispasr_set_gpu_backend(selected.lower().encode())
            params = OpenParams(2, options.threads, int(selected != "CPU"), 0, 1, -1)
            handle = self.lib.crispasr_session_open_with_params(
                str(options.model_path.resolve()).encode("utf-8"), b"qwen3", C.byref(params)
            )
            if not handle:
                raise ASRError(
                    "model_load_failed", "原生模型加载失败，请检查模型完整性和可用显存。"
                )
            try:
                actual = self.lib.lc_qwen_device(handle).decode()
                if actual.lower() != selected.lower():
                    raise ASRError(
                        "gpu_fallback_rejected", f"请求 {selected}，运行库实际使用 {actual}。"
                    )
                if self.lib.lc_qwen_set_greedy(handle, options.max_new_tokens) != 0:
                    raise ASRError("native_parameter_failed", "原生贪心解码设置失败。")
                return handle, actual, description
            except BaseException:
                self.lib.crispasr_session_close(handle)
                raise

    def recognize(self, handle, samples, language, context):
        if self.lib.lc_qwen_set_context(handle, context.encode("utf-8")) != 0:
            raise ASRError("native_parameter_failed", "上下文设置失败。")
        result = self.lib.crispasr_session_transcribe_lang(
            handle,
            samples.ctypes.data_as(C.POINTER(C.c_float)),
            len(samples),
            language.encode() if language else None,
        )
        if not result:
            raise ASRError("inference_failed", "原生推理失败，请检查可用显存与音频窗口。")
        try:
            text = "".join(
                self.lib.crispasr_session_result_segment_text(result, n).decode("utf-8")
                for n in range(self.lib.crispasr_session_result_n_segments(result))
            ).strip()
            detected = self.lib.lc_qwen_language(handle).decode().strip()
            return text, detected
        finally:
            self.lib.crispasr_session_result_free(result)

    def close(self, handle):
        self.lib.crispasr_session_close(handle)

    def warmup(self, handle, samples, max_tokens):
        """Compile the encode/decode paths with one token, then restore the request budget."""
        if self.lib.lc_qwen_set_greedy(handle, 1) != 0:
            raise ASRError("native_parameter_failed", "原生预热参数设置失败。")
        try:
            self.recognize(handle, samples, "zh", "")
        finally:
            if self.lib.lc_qwen_set_greedy(handle, max_tokens) != 0:
                raise ASRError("native_parameter_failed", "原生生成上限恢复失败。")
