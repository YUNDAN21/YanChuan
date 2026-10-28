"""Validate cross-engine text consistency and record the measured runtime size."""

import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    directory = args.root / "data/verification/native-qwen-20261027"
    engines = {
        name: read(directory / name / "report.json")
        for name in ("torch-bf16", "vulkan-f16", "vulkan-q8-isolated", "vulkan-q8-minimal")
    }
    for name in ("vulkan-f16", "vulkan-q8-isolated", "vulkan-q8-minimal"):
        log = (directory / name / "server.stderr.log").read_text(encoding="utf-8")
        if "using preferred GPU backend: Vulkan0" not in log or "RTX 5060" not in log:
            raise ValueError(f"Missing explicit NVIDIA Vulkan evidence: {name}")
    rows = []
    for language in ("zh", "en", "ja"):
        reference = engines["torch-bf16"]["samples"][language]["texts"][0]
        for name, report in engines.items():
            sample = report["samples"][language]
            if not sample["stable_text"] or any(text != reference for text in sample["texts"]):
                raise ValueError(f"Text differs from reference: {name}, {language}")
        rows.append(
            {
                "language": language,
                "matches_reference": True,
                "duration_seconds": engines["torch-bf16"]["samples"][language]["duration_seconds"],
                "warm_median_seconds": {
                    name: report["samples"][language]["warm_median_seconds"]
                    for name, report in engines.items()
                },
            }
        )
    runtime = args.root / "work/native-qwen/runtime-vulkan-minimal"
    manifest = read(runtime / "manifest.json")
    for item in manifest["files"]:
        path = runtime / item["name"]
        if path.stat().st_size != item["bytes"]:
            raise ValueError("Runtime manifest size mismatch")
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
                raise ValueError("Runtime manifest hash mismatch")
    result = {
        "success": True,
        "samples": rows,
        "runtime_bytes": manifest["runtime_bytes"],
        "runtime_archive_bytes": runtime.with_suffix(".zip").stat().st_size,
        "q8_model_bytes": (args.root / "work/native-qwen/qwen3-asr-0.6b-q8-crisp.gguf")
        .stat()
        .st_size,
        "memory": {name: report["gpu_memory"] for name, report in engines.items()},
        "notes": [
            "No capture or playback; all requests used local sample files.",
            "Three short clips establish consistency only, not corpus-wide accuracy.",
            "Warm medians exclude the first request per clip.",
            "GPU memory is total device usage; other graphics applications contribute.",
            "The initial concurrent Q8 run is excluded from this comparison.",
        ],
    }
    (directory / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
