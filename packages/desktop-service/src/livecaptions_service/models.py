"""Model catalogue, local inventory and cancellable downloads; no UI or ASR imports."""

import hashlib
import json
import multiprocessing
import queue
import threading
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path

from .native_assets import NATIVE_CATALOG, download_native_asset, locate_native


@dataclass(frozen=True)
class ModelAsset:
    id: str
    name: str
    kind: str
    repository: str = ""
    filename: str = ""
    url: str = ""
    sha256: str = ""


CATALOG = (
    *NATIVE_CATALOG,
    ModelAsset(
        "silero",
        "Silero VAD · 人声检测",
        "segmentation",
        filename="silero_vad.onnx",
        url="https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx",
        sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    ),
    ModelAsset(
        "smart-turn",
        "Smart Turn v3.2 · 语义断句",
        "segmentation",
        filename="smart-turn-v3.2-cpu.onnx",
        url="https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx",
        sha256="2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
    ),
)


def asset_by_id(identifier):
    try:
        return next(a for a in CATALOG if a.id == identifier)
    except StopIteration:
        raise ValueError("未知模型") from None


@dataclass(frozen=True)
class ModelSettings:
    selected_model: str = "qwen-0.6b"
    source: str = "huggingface"
    directory: str | None = None
    model_file: str | None = None

    def __post_init__(self):
        if asset_by_id(self.selected_model).kind != "asr":
            raise ValueError("请选择识别模型")
        if self.source not in {"modelscope", "huggingface"}:
            raise ValueError("不支持的模型下载源")
        if self.directory is not None and not Path(self.directory).is_absolute():
            raise ValueError("模型存放目录须为绝对路径")
        if self.model_file is not None and not Path(self.model_file).is_absolute():
            raise ValueError("模型文件须为绝对路径")

    def storage(self, root):
        return Path(self.directory) if self.directory else Path(root) / "models"

    @classmethod
    def load(cls, path):
        settings = cls(**json.loads(path.read_text("utf-8"))) if path.exists() else cls()
        return (
            replace(settings, source="huggingface") if settings.source == "modelscope" else settings
        )

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(path)


@lru_cache(maxsize=16)
def _hash_file(filename, size, modified):
    with Path(filename).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def local_endpoint_asset(asset, storage):
    path = Path(storage) / "endpoint" / asset.filename
    if path.is_file():
        stat = path.stat()
        if _hash_file(str(path), stat.st_size, stat.st_mtime_ns) == asset.sha256:
            return path
    return None


def download_endpoint_asset(asset, storage, source, progress):
    """Verify an endpoint artifact before atomic replacement."""
    existing = local_endpoint_asset(asset, storage)
    if existing:
        progress({"message": asset.name + " 已在本地，跳过下载"})
        return existing
    storage = Path(storage)
    from urllib.request import urlopen

    destination = storage / "endpoint" / asset.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    digest, current = hashlib.sha256(), 0
    try:
        with urlopen(asset.url, timeout=30) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length", 0))
            while chunk := response.read(256 * 1024):
                output.write(chunk)
                digest.update(chunk)
                current += len(chunk)
                progress({"message": asset.filename, "current": current, "total": total})
        if digest.hexdigest() != asset.sha256:
            raise ValueError("下载校验失败，请重试")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


local_asset = locate_native
download_asset = download_native_asset


def _download_process(
    identifiers,
    storage,
    source,
    output,
    catalog=CATALOG,
    downloader=download_asset,
    model_file=None,
    selected_model=None,
):
    """Spawn target. SDK output is diagnostic, never parent JSONL traffic."""
    import os
    import sys
    import time

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - process lifetime stream
    sys.stdout = sys.stderr
    last = 0.0

    def progress(payload):
        nonlocal last
        now = time.monotonic()
        if now - last >= 0.2:
            last = now
            output.put({"state": "downloading", **payload})

    try:
        for identifier in identifiers:
            asset = next(asset for asset in catalog if asset.id == identifier)
            output.put({"state": "downloading", "message": "准备 " + asset.name})
            destination = model_file if model_file and identifier == selected_model else storage
            downloader(asset, destination, source, progress)
        output.put({"state": "completed", "message": "所选识别模型和分段模型已就绪"})
    except Exception:  # noqa: BLE001 - do not transmit SDK credentials or raw HTTP errors
        output.put({"state": "failed", "message": "模型下载失败，请检查网络或切换下载源后重试"})


class ModelManager:
    def __init__(
        self,
        root,
        on_update=None,
        *,
        catalog=CATALOG,
        locator=local_asset,
        downloader=download_asset,
        sources=("huggingface",),
        runtime="crispasr-0.8.45-lc1",
    ):
        self.root = Path(root)
        self.path = self.root / "data/model-settings.json"
        self.settings = ModelSettings.load(self.path)
        self.on_update = on_update or (lambda payload: None)
        self.catalog, self.locator, self.downloader = catalog, locator, downloader
        self.sources, self.runtime = sources, runtime
        self._lock = threading.RLock()
        self._process = None
        self._thread = None
        self.state = {"state": "idle", "message": ""}

    @property
    def busy(self):
        return self.state["state"] == "downloading"

    def inventory(self):
        with self._lock:
            storage = self.settings.storage(self.root)
            assets = []
            for asset in self.catalog:
                location = (
                    self.settings.model_file
                    if self.settings.model_file and asset.id == self.settings.selected_model
                    else storage
                )
                path = self.locator(asset, location)
                assets.append(
                    {
                        "id": asset.id,
                        "name": asset.name,
                        "kind": asset.kind,
                        "ready": path is not None,
                        "path": str(path) if path else "",
                    }
                )
            return {
                "settings": asdict(self.settings),
                "directory": str(storage),
                "assets": assets,
                "download": dict(self.state),
                "sources": self.sources,
                "runtime": self.runtime,
            }

    def configure(self, settings):
        with self._lock:
            if self.busy:
                raise RuntimeError("请先完成或取消当前模型下载")
            if settings.model_file:
                asset = next(asset for asset in self.catalog if asset.id == settings.selected_model)
                if self.locator(asset, settings.model_file) is None:
                    raise ValueError("所选模型文件与当前运行时、型号或精度不兼容")
            if settings.source not in self.sources:
                raise ValueError("请选择可用的模型下载源")
            settings.save(self.path)
            self.settings = settings

    def start(self):
        with self._lock:
            if self.busy:
                raise RuntimeError("模型正在下载")
            context = multiprocessing.get_context("spawn")
            output = context.Queue()
            identifiers = [self.settings.selected_model, "silero", "smart-turn"]
            process = context.Process(
                target=_download_process,
                args=(
                    identifiers,
                    str(self.settings.storage(self.root)),
                    self.settings.source,
                    output,
                    self.catalog,
                    self.downloader,
                    self.settings.model_file,
                    self.settings.selected_model,
                ),
                daemon=True,
            )
            self._process = process
            self.state = {"state": "downloading", "message": "正在准备下载"}
            process.start()
            self._thread = threading.Thread(
                target=self._observe, args=(process, output), daemon=True
            )
            self._thread.start()
            return dict(self.state)

    def _observe(self, process, output):
        terminal = False
        try:
            while True:
                try:
                    value = output.get(timeout=0.2)
                except queue.Empty:
                    if process.is_alive():
                        continue
                    break
                with self._lock:
                    if self._process is not process:
                        return
                    self.state = value
                    self.on_update(dict(value))
                if value["state"] in {"completed", "failed"}:
                    terminal = True
                    break
            with self._lock:
                if not terminal and self._process is process:
                    self.state = {"state": "failed", "message": "下载进程已结束，可重试"}
                    self.on_update(dict(self.state))
        finally:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            output.close()

    def cancel(self):
        with self._lock:
            if not self.busy:
                return
            process, self._process = self._process, None
            self.state = {"state": "cancelled", "message": "下载已取消；重试会复用已下载的缓存"}
            self.on_update(dict(self.state))
        if process and process.is_alive():
            process.terminate()
            process.join(timeout=2)

    def close(self):
        self.cancel()
