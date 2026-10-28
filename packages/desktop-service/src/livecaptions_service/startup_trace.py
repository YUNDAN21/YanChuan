"""Application-owned timing persistence; core observations remain I/O free."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from livecaptions_asr.startup import StartupEvent, StartupObserver

logger = logging.getLogger(__name__)

STAGE_LABELS = {
    "startup_total": "启动字幕",
    "runtime_setup": "装配运行环境",
    "capture_import": "导入采集依赖",
    "recognizer_import": "导入识别接口",
    "segmentation_prepare": "准备分段模型",
    "endpoint_import": "导入检测接口",
    "vad_prepare": "加载 Silero",
    "endpoint_prepare": "加载 Smart Turn",
    "recognizer_prepare": "准备识别模型",
    "recognizer_queue": "等待识别资源",
    "engine_load": "加载识别模型",
    "native_model_validate": "校验 GGUF 格式",
    "native_library": "加载原生运行库",
    "native_weights": "将模型装入设备",
    "native_model": "复用原生模型",
    "warmup": "首次推理预热",
    "stream_setup": "创建字幕会话",
    "audio_start": "打开音频设备",
}


class StartupTrace:
    """One ID per start attempt. Total ends at device-ready, before transcription."""

    def __init__(self, path: Path, observer: StartupObserver):
        self.path, self.observer = path, observer
        self.id = uuid4().hex
        self.started = perf_counter()
        self.finished = False
        self.events: list[dict] = []
        self.emit(StartupEvent("startup_total", "started"))

    def emit(self, event: StartupEvent):
        if self.finished and event.stage != "startup_total":
            return
        record = {
            "attempt": self.id,
            "timestamp": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(perf_counter() - self.started, 4),
            **event.to_dict(),
        }
        self.events.append(record)
        logger.info(
            "startup %s %s %.4fs (elapsed %.4fs) [%s]",
            event.stage,
            event.state,
            event.seconds,
            record["elapsed_seconds"],
            self.id,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Cannot write startup timing log: %s", self.path)
        self.observer(event)

    def finish(self, state="completed"):
        if self.finished:
            return
        self.finished = True
        self.emit(StartupEvent("startup_total", state, round(perf_counter() - self.started, 4)))

    def state_handler(self, target: Callable[[str], None]) -> Callable[[str], None]:
        def state(value):
            if value == "recording":
                self.finish()
            target(value)

        return state
