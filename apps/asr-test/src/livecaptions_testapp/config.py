from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVECAPTIONS_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    model_id: str = "Qwen/Qwen3-ASR-0.6B"
    model_path: Path = Path("models/native/qwen3-asr-0.6b-q8_0.gguf")
    library_path: Path = Path("runtime/native/win-x64/crispasr.dll")
    device: str = "vulkan"
    precision: Literal["q8_0", "f16"] = "q8_0"
    threads: int = Field(default=4, ge=1)
    max_new_tokens: int = Field(default=512, ge=1, le=4096)
    max_upload_mb: int | None = Field(default=None, ge=1)
    max_audio_seconds: float | None = Field(default=None, gt=0)
    recording_limit_seconds: float | None = Field(default=None, gt=0)
    capture_buffer_seconds: float = Field(default=90, ge=30, le=600)
    capture_idle_seconds: float = Field(default=120, ge=5, le=600)
    archive_chunk_seconds: float = Field(default=30, ge=5, le=300)
    file_chunk_seconds: float = Field(default=20, ge=1, le=30)
    semantic_max_seconds: float = Field(default=60, ge=10, le=180)
    data_dir: Path = Path("data")
    max_inflight_requests: int = Field(default=2, ge=1, le=16)
    live_update_seconds: float = Field(default=1, ge=0.5, le=5)
    live_segment_seconds: float = Field(default=16, ge=3, le=60)
    live_silence_seconds: float = Field(default=0.6, ge=0.2, le=2)
    live_energy_threshold: float = Field(default=0.003, gt=0, le=0.1)
    live_segmentation: Literal["model", "energy"] = "model"
    live_max_wait_seconds: float = Field(default=2, ge=0.2, le=10)
    endpoint_threshold: float = Field(default=0.5, gt=0, le=1)
    speech_threshold: float = Field(default=0.5, gt=0, le=1)
    vad_model_path: Path = Path("models/endpoint/silero_vad.onnx")
    endpoint_model_path: Path = Path("models/endpoint/smart-turn-v3.2-cpu.onnx")

    @property
    def max_upload_bytes(self) -> int | None:
        return self.max_upload_mb * 1024 * 1024 if self.max_upload_mb is not None else None
