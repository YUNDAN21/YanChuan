"""Endpoint model assets and ONNX validation, independent of ASR asset handling."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen


@dataclass(frozen=True)
class ModelAsset:
    id: str
    name: str
    kind: str
    repository: str = ""
    filename: str = ""
    url: str = ""
    size: int = 0


ENDPOINT_CATALOG = (
    ModelAsset(
        "silero",
        "Silero VAD · 人声检测",
        "segmentation",
        filename="silero_vad.onnx",
        url="https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx",
        size=2327524,
    ),
    ModelAsset(
        "smart-turn",
        "Smart Turn v3.2 · 语义断句",
        "segmentation",
        filename="smart-turn-v3.2-cpu.onnx",
        url="https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx",
        size=8679182,
    ),
)


@lru_cache(maxsize=16)
def _endpoint_compatible(filename, size, modified, identifier):
    """Cache runtime/schema checks by file metadata; never compute digests."""
    from livecaptions_asr.endpoints.runtime import cpu_session

    session = cpu_session(Path(filename), 1)
    inputs = {item.name for item in session.get_inputs()}
    required = {"input", "state", "sr"} if identifier == "silero" else {"input_features"}
    return required <= inputs


def validate_endpoint(asset, path):
    if not path.is_file():
        return False
    stat = path.stat()
    if stat.st_size != asset.size:
        return False
    try:
        return _endpoint_compatible(str(path), stat.st_size, stat.st_mtime_ns, asset.id)
    except Exception:  # noqa: BLE001 - invalid ONNX/runtime failures are not ready assets
        return False


def local_endpoint_asset(asset, storage):
    path = Path(storage) / "endpoint" / asset.filename
    return path if validate_endpoint(asset, path) else None


def download_endpoint_asset(asset, storage, source, progress):
    """Check fixed size and ONNX input schema before atomic replacement."""
    existing = local_endpoint_asset(asset, storage)
    if existing:
        progress({"message": asset.name + " 已在本地，跳过下载"})
        return existing
    destination = Path(storage) / "endpoint" / asset.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    current = 0
    try:
        with urlopen(asset.url, timeout=30) as response, temporary.open("wb") as output:
            total = asset.size
            while chunk := response.read(256 * 1024):
                if current + len(chunk) > total:
                    raise ValueError("检测模型下载超过声明大小")
                output.write(chunk)
                current += len(chunk)
                progress({"message": asset.filename, "current": current, "total": total})
        if not validate_endpoint(asset, temporary):
            raise ValueError("下载校验失败，请重试")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
