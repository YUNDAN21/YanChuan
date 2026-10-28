"""Pinned native Qwen catalogue and resumable downloads."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class NativeAsset:
    id: str
    name: str
    repository: str
    revision: str
    filename: str
    size: int
    sha256: str
    runtime: str = "crispasr-0.8.45-lc1"
    precision: str = "q8_0"
    source: str = "huggingface"
    kind: str = "asr"

    @property
    def url(self):
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.filename}?download=true"


NATIVE_CATALOG = (
    NativeAsset(
        "qwen-0.6b",
        "Qwen3-ASR 0.6B Q8 · 推荐",
        "cstr/qwen3-asr-0.6b-GGUF",
        "58bd3202f835f46b24b17142cb503b0860c737a5",
        "qwen3-asr-0.6b-q8_0.gguf",
        1006809760,
        "f547589d5ca582e093b2d3312ad9ff13b609b43d413f972c0e92b823dde70a00",
    ),
    NativeAsset(
        "qwen-1.7b",
        "Qwen3-ASR 1.7B Q8 · 更高资源占用",
        "cstr/qwen3-asr-1.7b-GGUF",
        "674df5d44b50a63e7102a18895ed20e3f91de301",
        "qwen3-asr-1.7b-q8_0.gguf",
        2506723200,
        "9851ab996591a2d0cb0efb216002764b509c86bd40c95e613d7b65b8e69c8a6e",
    ),
)


def verified_file(path, size, sha256):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == sha256


def download_verified(url, destination, size, sha256, progress):
    """Resume a public file; validate HTTP ranges, byte count and digest before publication."""
    destination = Path(destination)
    if verified_file(destination, size, sha256):
        progress({"current": size, "total": size, "message": "模型已就绪，跳过下载"})
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".download")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        with partial.open("wb"):
            pass
        offset = 0
    if offset < size:
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers)
        with urlopen(request, timeout=30) as response:
            if response.status == 206:
                match = re.fullmatch(
                    r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                )
                if (
                    not match
                    or int(match[1]) != offset
                    or int(match[3]) != size
                    or int(match[2]) < offset
                    or int(match[2]) >= size
                ):
                    raise ValueError("下载响应的分段范围不匹配")
            elif response.status == 200:
                offset = 0  # server ignored Range: replace the partial, never append a second file
            else:
                raise ValueError("不支持的下载响应")
            current = offset
            with partial.open("ab" if offset else "wb") as output:
                while chunk := response.read(256 * 1024):
                    if current + len(chunk) > size:
                        raise ValueError("下载数据超过声明大小")
                    output.write(chunk)
                    current += len(chunk)
                    progress({"current": current, "total": size, "message": destination.name})
    if not verified_file(partial, size, sha256):
        # A short transfer remains resumable. A complete but corrupt one must restart.
        if partial.stat().st_size == size:
            partial.replace(partial.with_suffix(".invalid"))
        raise ValueError("模型下载校验失败，请重试")
    partial.replace(destination)
    progress({"current": size, "total": size, "message": "模型下载校验完成"})
    return destination


def locate_native(asset, storage):
    """Locate only structurally compatible GGUF; old safetensors never count as ready."""
    from livecaptions_asr import ASRError
    from livecaptions_asr.engines.gguf_model import inspect_qwen_gguf

    from .models import _hash_file, local_endpoint_asset

    if asset.kind != "asr":
        return local_endpoint_asset(asset, storage)
    storage = Path(storage)
    preferred = (
        [storage]
        if storage.is_file()
        else [storage / "native" / asset.filename, storage / asset.filename]
    )
    others = (
        []
        if storage.is_file()
        else sorted(storage.glob("*.gguf")) + sorted((storage / "native").glob("*.gguf"))
    )
    expected_size = "1.7B" if asset.id == "qwen-1.7b" else "0.6B"
    for path in dict.fromkeys([*preferred, *others]):
        if not path.is_file():
            continue
        if path.name == asset.filename:
            stat = path.stat()
            if (
                stat.st_size != asset.size
                or _hash_file(str(path), stat.st_size, stat.st_mtime_ns) != asset.sha256
            ):
                continue
        try:
            info = inspect_qwen_gguf(path)
        except ASRError:
            continue
        if info["model_size"] == expected_size and info["precision"] == asset.precision:
            return path
    return None


def download_native_asset(asset, storage, source, progress):
    from .models import download_endpoint_asset

    if asset.kind != "asr":
        return download_endpoint_asset(asset, storage, "huggingface", progress)
    if source != "huggingface":
        raise ValueError("原生 GGUF 当前仅提供已验证的 Hugging Face 源")
    existing = locate_native(asset, storage)
    if existing:
        progress({"message": "兼容 GGUF 已就绪，跳过下载"})
        return existing
    if Path(storage).suffix.lower() == ".gguf":
        raise ValueError("所选 GGUF 不兼容或不存在；请重新选择文件或清空文件选择后下载。")
    return download_verified(
        asset.url, Path(storage) / "native" / asset.filename, asset.size, asset.sha256, progress
    )
