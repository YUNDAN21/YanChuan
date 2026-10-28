"""Application-owned shared models and per-session capture/segmentation."""

import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from livecaptions_audio.session import SessionRunner

from livecaptions_asr.startup import StartupEvent, StartupObserver, StartupTimer

from .models import ModelSettings, asset_by_id, local_asset


@dataclass(frozen=True)
class InputSelection:
    source: str = "system"
    microphone_id: str | None = None
    system_id: str | None = None
    language: str = "auto"

    def __post_init__(self):
        if self.source not in {"system", "microphone", "both"}:
            raise ValueError("不支持的音源")
        if self.language not in {"auto", "zh", "ja", "en"}:
            raise ValueError("不支持的语言")


class DesktopRuntime:
    def __init__(
        self,
        root: Path,
        *,
        library_path=None,
        device="vulkan",
        on_progress: StartupObserver | None = None,
    ):
        self.root = root
        self.device = device
        project_root = Path(__file__).resolve().parents[4]
        bundled = Path(sys.executable).resolve().parent.parent / "native/crispasr.dll"
        self.library_path = (
            Path(library_path)
            if library_path
            else (
                bundled
                if bundled.is_file()
                else project_root / "runtime/native/win-x64/crispasr.dll"
            )
        )
        self._model_lock = threading.RLock()
        self._recognizer = self._vad = self._endpoint = None
        self.on_progress = on_progress
        self.model_settings = ModelSettings.load(root / "data/model-settings.json")

    def configure_models(self, settings):
        with self._model_lock:
            if settings != self.model_settings:
                self.close()
                self.model_settings = settings

    def close(self):
        with self._model_lock:
            if self._recognizer is not None:
                self._recognizer.close()
                self._recognizer = None
            self._vad = self._endpoint = None

    def _report(self, event: StartupEvent):
        if self.on_progress is not None:
            self.on_progress(event)

    def _segmenter(self):
        timer = StartupTimer(self._report)
        with timer.phase("endpoint_import"):
            from livecaptions_asr import ModelSegmenter, SileroVADModel, SmartTurnEndpoint

        for stage, attribute, create in [
            (
                "vad_prepare",
                "_vad",
                lambda: SileroVADModel.from_path(
                    self.model_settings.storage(self.root) / "endpoint/silero_vad.onnx"
                ),
            ),
            (
                "endpoint_prepare",
                "_endpoint",
                lambda: SmartTurnEndpoint.from_path(
                    self.model_settings.storage(self.root) / "endpoint/smart-turn-v3.2-cpu.onnx"
                ),
            ),
        ]:
            if getattr(self, attribute) is None:
                with timer.phase(stage):
                    model = create()
                    model.prepare()
                    setattr(self, attribute, model)
            else:
                timer.emit(stage, "reused")
        return ModelSegmenter(self._vad.create_detector(), self._endpoint)

    def recognizer(self):
        timer = StartupTimer(self._report)
        with timer.phase("recognizer_import"):
            from livecaptions_asr import Recognizer
            from livecaptions_asr.engines.native_options import NativeQwenOptions
            from livecaptions_asr.engines.native_qwen import NativeQwenEngine

        with self._model_lock:
            if self._recognizer is None:
                asset = asset_by_id(self.model_settings.selected_model)
                path = local_asset(
                    asset, self.model_settings.model_file or self.model_settings.storage(self.root)
                )
                if path is None:
                    raise RuntimeError(
                        "识别模型尚未下载，请在“模型管理”中下载或选择兼容 GGUF；旧 safetensors 缓存继续保留"
                    )
                self._recognizer = Recognizer(
                    NativeQwenEngine(
                        NativeQwenOptions(
                            model_id="Qwen/Qwen3-ASR-"
                            + ("1.7B" if asset.id == "qwen-1.7b" else "0.6B"),
                            model_path=path,
                            library_path=self.library_path,
                            device=self.device,
                            precision=asset.precision,
                        ),
                        on_progress=self._report,
                    )
                )
            return self._recognizer

    def runner(self, selection: InputSelection) -> SessionRunner:
        with StartupTimer(self._report).phase("capture_import"):
            from livecaptions_audio import WasapiRecording
        return SessionRunner(
            self.recognizer(),
            self._segmenter,
            lambda: WasapiRecording(
                selection.source,
                selection.microphone_id,
                selection.system_id,
                None,
                buffer_seconds=90,
            ),
        )
