"""Explicit native runtime options; no application paths or environment configuration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..domain import ASRError


@dataclass(frozen=True)
class NativeQwenOptions:
    model_path: Path
    library_path: Path
    model_id: str = "Qwen/Qwen3-ASR-0.6B"
    device: str = "vulkan"
    precision: Literal["q8_0", "f16"] = "q8_0"
    threads: int = 4
    max_new_tokens: int = 512

    def __post_init__(self):
        object.__setattr__(self, "model_path", Path(self.model_path))
        object.__setattr__(self, "library_path", Path(self.library_path))
        if (
            self.device != "cpu"
            and self.device != "vulkan"
            and not (self.device.startswith("Vulkan") and self.device[6:].isdigit())
        ):
            raise ASRError("invalid_options", "原生设备须为 vulkan、VulkanN 或 cpu。")
        if self.precision not in {"q8_0", "f16"}:
            raise ASRError("invalid_options", "不支持的模型精度。")
        if (
            type(self.threads) is not int
            or self.threads < 1
            or type(self.max_new_tokens) is not int
            or not 1 <= self.max_new_tokens <= 4096
        ):
            raise ASRError("invalid_options", "线程数须为正整数，生成上限须为 1–4096。")
