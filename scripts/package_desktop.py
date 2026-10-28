"""Build a relocatable Windows x64 release from an explicit runtime allowlist."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT_MODULES = {
    "livecaptions-asr": ("src", "livecaptions_asr"),
    "livecaptions-audio": ("packages/audio-capture/src", "livecaptions_audio"),
    "livecaptions-meetings": ("packages/meetings/src", "livecaptions_meetings"),
    "livecaptions-translation": ("packages/translation/src", "livecaptions_translation"),
    "livecaptions-service": ("packages/desktop-service/src", "livecaptions_service"),
    "livecaptions-backend": ("apps/desktop-backend/src", "livecaptions_backend"),
}
BLOCKED_NAMES = {
    ".env",
    "meeting-settings.json",
    "meeting-appearance.json",
    "caption-settings.json",
    "model-settings.json",
    "meeting.sqlite3",
    "direct_url.json",
    "pyvenv.cfg",
}
NATIVE_FILES = {
    "crispasr.dll",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-cpu.dll",
    "ggml-vulkan.dll",
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcomp140.dll",
    "LICENSE",
    "GGML-LICENSE",
    "THIRD_PARTY_NOTICES.txt",
    "VC-REDIST.txt",
}


def copy_native_runtime(root, output):
    source = root / "runtime/native/win-x64"
    manifest = json.loads((source / "runtime-manifest.json").read_text("utf-8"))
    records = {record["name"]: record for record in manifest["files"]}
    if set(records) != NATIVE_FILES:
        raise ValueError("Native manifest does not match the release allowlist")
    for name in sorted(NATIVE_FILES):
        path = source / name
        if not path.is_file():
            raise ValueError("Missing native runtime file: " + name)
        copy_file(path, output / "backend/native" / name)
    copy_file(source / "runtime-manifest.json", output / "backend/native/runtime-manifest.json")


def runtime_distributions(roots=None):
    """Expand installed Requires-Dist, including requested endpoint extras."""
    selected = {name: set() for name in (roots or ["livecaptions-backend"])}
    pending, expanded = list(selected), {}
    while pending:
        name = pending.pop()
        extras = selected[name]
        if expanded.get(name) == extras:
            continue
        expanded[name] = set(extras)
        distribution = metadata.distribution(name)
        for text in distribution.requires or []:
            requirement = Requirement(text)
            if requirement.marker and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in {"", *extras}
            ):
                continue
            dependency = canonicalize_name(requirement.name)
            combined = selected.get(dependency, set()) | set(requirement.extras)
            if dependency not in selected or combined != selected[dependency]:
                selected[dependency] = combined
                pending.append(dependency)
    return [metadata.distribution(name) for name in sorted(selected)]


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_tree(source, destination, *, exclude=()):
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_file() and not any(p in {"__pycache__", *exclude} for p in relative.parts):
            copy_file(path, destination / relative)


def copy_python(root, output, *, dependency_roots=None):
    base = Path(sys.base_prefix)
    python = output / "backend/python"
    python.mkdir(parents=True, exist_ok=True)
    for source in base.iterdir():
        if source.is_file() and (
            source.suffix.lower() in {".exe", ".dll"} or source.name == "LICENSE.txt"
        ):
            copy_file(source, python / source.name)
    for name in ("Lib", "DLLs"):
        if (base / name).is_dir():
            copy_tree(
                base / name, python / name, exclude={"site-packages", "test", "tests", "ensurepip"}
            )
    site = python / "Lib/site-packages"
    versions = []
    for distribution in runtime_distributions(dependency_roots):
        name = canonicalize_name(distribution.metadata["Name"])
        versions.append({"name": name, "version": distribution.version})
        location = Path(distribution.locate_file("")).resolve()
        if name in PROJECT_MODULES:
            package_root, module = PROJECT_MODULES[name]
            copy_tree(root / package_root / module, site / module)
        for relative in distribution.files or []:
            path = Path(distribution.locate_file(relative))
            if not path.is_file() or not path.resolve().is_relative_to(location):
                continue
            parts = Path(relative).parts
            if "__pycache__" in parts or path.name in BLOCKED_NAMES:
                continue
            if path.suffix.lower() in {".wav", ".flac", ".safetensors", ".gguf"}:
                previous = site / Path(relative)
                if previous.exists() and previous.resolve().is_relative_to(output):
                    previous.unlink()
                continue
            if name in PROJECT_MODULES and not any(p.endswith(".dist-info") for p in parts):
                continue
            # Never carry developer-generated executable wrappers, caches or editable .pth files.
            if path.suffix in {".pyc", ".pyo"}:
                continue
            copy_file(path, site / Path(relative))
    (output / "THIRD-PARTY.json").write_text(json.dumps(versions, indent=2), "utf-8")
    return versions


def local_secrets(root):
    settings = root / "data/meeting-settings.json"
    if not settings.is_file():
        return []

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"api_key", "base_url"} and isinstance(item, str) and len(item) >= 12:
                    yield item.encode("utf-8")
                else:
                    yield from collect(item)
        elif isinstance(value, list):
            for item in value:
                yield from collect(item)

    return list(set(collect(json.loads(settings.read_text("utf-8")))))


def audit_release(output, secrets, *, native_only=False):
    """Scan for local credentials, state and weights; list paths and sizes without hashes."""
    files, total = [], 0
    keep = max([len(value) for value in secrets] + [1]) - 1
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(output)
        if (
            relative.parts[0] in {"data", "models", "work", ".git", ".venv", "archive", "tests"}
            or path.name in BLOCKED_NAMES
            or path.suffix.lower() in {".safetensors", ".gguf", ".wav", ".flac"}
            or path.name in {"silero_vad.onnx", "smart-turn-v3.2-cpu.onnx"}
        ):
            raise ValueError("Release contains an excluded asset: " + str(relative))
        if native_only:
            lower = relative.as_posix().lower()
            parts = {part.lower() for part in relative.parts}
            package_parts = [part.lower() for part in relative.parts]
            package = (
                package_parts[package_parts.index("site-packages") + 1]
                if "site-packages" in package_parts
                and package_parts.index("site-packages") + 1 < len(package_parts)
                else ""
            )
            if (
                package in {"torch", "transformers", "qwen_asr", "triton"}
                or parts & {"cuda", "sdk"}
                or any(part.startswith(("torch-", "transformers-", "qwen_asr-")) for part in parts)
                or path.name.lower().startswith(
                    ("cublas", "cudnn", "cufft", "curand", "cusparse", "nvrtc", "cudart")
                )
                or lower.endswith(
                    (
                        "livecaptions_asr/engines/qwen.py",
                        "livecaptions_asr/engines/options.py",
                        "livecaptions_asr/model_store.py",
                    )
                )
            ):
                raise ValueError("Native release contains an obsolete runtime: " + str(relative))
        tail = b""
        with path.open("rb") as source:
            while block := source.read(4 * 1024 * 1024):
                if any(secret in tail + block for secret in secrets):
                    raise ValueError(
                        "Release contains local credentials or private API configuration: "
                        + str(relative)
                    )
                tail = block[-keep:] if keep else b""
        size = path.stat().st_size
        total += size
        files.append({"path": relative.as_posix(), "bytes": size})
    return {
        "format": 1,
        "architecture": "win-x64",
        "files": files,
        "bytes": total,
        "local_models_included": False,
        "local_configuration_included": False,
        "credential_scan_passed": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an unarchived build directory with a runtime marker",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or root / "dist" / ("YanChuan-win-x64-" + stamp)).resolve()
    resumable = args.resume and (output / "yanchuan.runtime.json").is_file()
    archive_path = output.parent / (output.name + ".zip")
    if (output.exists() and not resumable) or archive_path.exists():
        raise FileExistsError("Release output already exists; choose a new output path")
    output.mkdir(parents=True, exist_ok=resumable)
    publish = root / "work" / ("publish-" + stamp)
    vswhere = (
        Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    msbuild = subprocess.check_output(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.Component.MSBuild",
            "-find",
            "MSBuild/**/Bin/MSBuild.exe",
        ],
        text=True,
    ).splitlines()[0]
    print("Publishing self-contained WinUI...", flush=True)
    subprocess.run(
        [
            msbuild,
            str(root / "apps/desktop-winui/LiveCaptions.csproj"),
            "/restore",
            "/t:Publish",
            "/p:Configuration=Release",
            "/p:Platform=x64",
            "/p:SelfContained=true",
            "/p:PublishDir=" + str(publish) + os.sep,
            "/verbosity:minimal",
            "/nologo",
        ],
        check=True,
        cwd=root,
    )
    copy_tree(publish, output)
    print("Copying standalone Python and runtime dependencies...", flush=True)
    versions = copy_python(root, output)
    copy_native_runtime(root, output)
    (output / "yanchuan.runtime.json").write_text(
        json.dumps(
            {
                "format": 1,
                "version": "0.1.0",
                "backend": "backend/python/python.exe",
                "architecture": "win-x64",
                "native_runtime": "backend/native/crispasr.dll",
            },
            indent=2,
        ),
        "utf-8",
    )
    (output / "使用说明.txt").write_text(
        "言川（YanChuan）Windows x64 便携版\n\n解压全部文件后，双击 YanChuan.exe。无需安装 Python、.NET 或 Visual Studio。\n"
        "首次打开：在左侧“模型管理”中下载 Qwen3-ASR Q8 GGUF，或选择已有兼容 GGUF。旧 safetensors 不用于本引擎。\n"
        "本包不含识别模型、分段模型、API 密钥、私人 API 地址、会议记录、录音或本地配置。\n"
        "使用云端总结/问答/翻译时，请在“API 配置”中自行添加服务。\n"
        "配置与会议保存在 %LOCALAPPDATA%\\YanChuan\\data（已有安装沿用原 LiveCaptions 数据目录），默认模型下载到其 models 目录。\n"
        "默认使用 Vulkan 独立显卡，需要支持 Vulkan 的显卡驱动；无需 CUDA Toolkit 或 Vulkan SDK。GPU 初始化失败会报告错误。\n",
        "utf-8",
    )
    print("Auditing release paths and scanning every file for local secrets...", flush=True)
    report = audit_release(output, local_secrets(root), native_only=True)
    report["runtime_distributions"] = len(versions)
    report["dependencies"] = versions
    if report["bytes"] > 1024**3:
        raise ValueError("Unpacked release exceeds 1 GiB")
    (output / "release-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        f"Packing {len(report['files'])} files / {report['bytes'] / 1024**3:.2f} GiB...", flush=True
    )
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True
    ) as archive:
        last = time.monotonic()
        for index, path in enumerate(sorted(output.rglob("*"))):
            if path.is_file():
                archive.write(path, Path(output.name) / path.relative_to(output))
            if time.monotonic() - last >= 15:
                print(f"Archive progress: {index} paths", flush=True)
                last = time.monotonic()
    if archive_path.stat().st_size > 512 * 1024**2:
        raise ValueError("Release ZIP exceeds 512 MiB")
    print(
        json.dumps(
            {
                "directory": str(output),
                "archive": str(archive_path),
                "archive_bytes": archive_path.stat().st_size,
                "credential_scan_passed": True,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
