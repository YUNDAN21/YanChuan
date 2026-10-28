"""Copy an explicit native-runtime allowlist; never include models or local settings."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--redist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for name in (
        "crispasr.exe",
        "crispasr.dll",
        "ggml.dll",
        "ggml-base.dll",
        "ggml-cpu.dll",
        "ggml-vulkan.dll",
        "LICENSE",
        "THIRD_PARTY_NOTICES.txt",
    ):
        shutil.copy2(args.source / name, args.output / name)
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copy2(args.redist / "x64/Microsoft.VC143.CRT" / name, args.output / name)
    shutil.copy2(
        args.redist / "x64/Microsoft.VC143.OpenMP/vcomp140.dll", args.output / "vcomp140.dll"
    )
    manifest = []
    for path in sorted(args.output.iterdir()):
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        manifest.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
    report = {
        "runtime_bytes": sum(item["bytes"] for item in manifest),
        "files": manifest,
        "models_included": False,
        "configuration_included": False,
        "source_release": "CrispStrobe/CrispASR v0.8.45 Windows x86_64 Vulkan",
    }
    (args.output / "manifest.json").write_text(json.dumps(report, indent=2), "utf-8")
    archive = args.output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in sorted(args.output.iterdir()):
            package.write(path, args.output.name + "/" + path.name)
    report["archive_bytes"] = archive.stat().st_size
    print(json.dumps({key: value for key, value in report.items() if key != "files"}))


if __name__ == "__main__":
    main()
