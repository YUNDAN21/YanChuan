"""Build a per-user Windows installer from an audited, already published portable payload."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from package_desktop import audit_release, local_secrets


def nsis_string(value):
    value = str(value)
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("Invalid installer path")
    return value.replace("$", "$$").replace('"', '$\\"')


def verified_payload(package, secrets):
    package = package.resolve()
    manifest = json.loads((package / "release-manifest.json").read_text("utf-8"))
    marker = json.loads((package / "yanchuan.runtime.json").read_text("utf-8"))
    if marker.get("native_runtime") != "backend/native/crispasr.dll":
        raise ValueError("Installer requires the native Vulkan release")
    for path in package.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(package):
            raise ValueError("Payload must not contain links outside its directory")
    audited = audit_release(package, secrets, native_only=True)
    expected = {
        item["path"]: {"path": item["path"], "bytes": item["bytes"]} for item in manifest["files"]
    }
    actual = {
        item["path"]: item for item in audited["files"] if item["path"] != "release-manifest.json"
    }
    if actual != expected:
        raise ValueError("Payload differs from its release manifest")
    return marker, audited


def payload_include(package, output, marker, audited):
    version = marker["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a three-part numeric release version")
    lines = [
        f'!define APP_VERSION "{version}"',
        f'!define APP_VERSION_QUAD "{version}.0"',
        f'!define SETUP_OUTPUT "{nsis_string(output)}"',
        f'!define APP_ICON "{nsis_string(package / "Assets/AppIcon.ico")}"',
        f"!define PAYLOAD_KIB {(audited['bytes'] + 1023) // 1024}",
        "!macro InstallPayload",
    ]
    directories = set()
    previous = None
    for item in audited["files"]:
        relative = Path(item["path"])
        folder = relative.parent
        if folder != previous:
            suffix = "" if folder == Path(".") else "\\" + nsis_string(str(folder))
            lines.append(f'  SetOutPath "$INSTDIR{suffix}"')
            previous = folder
        lines.append(f'  File "{nsis_string(package / relative)}"')
        directories.update(parent for parent in relative.parents if parent != Path("."))
    lines.extend(["!macroend", "!macro UninstallPayload"])
    for item in audited["files"]:
        relative = nsis_string(str(Path(item["path"])))
        lines.append(f'  Delete "$INSTDIR\\{relative}"')
    for directory in sorted(directories, key=lambda p: (-len(p.parts), str(p))):
        lines.append(f'  RMDir "$INSTDIR\\{nsis_string(directory)}"')
    lines.append("!macroend")
    return "\n".join(lines) + "\n"


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    package, compiler = args.package.resolve(), args.compiler.resolve()
    marker, audited = verified_payload(package, local_secrets(root))
    output = (
        args.output or root / "dist" / f"YanChuan-{marker['version']}-Setup-x64.exe"
    ).resolve()
    if output.exists():
        raise FileExistsError("Installer already exists; choose a new output")
    build = root / "work/installer-build" / output.stem
    build.mkdir(parents=True, exist_ok=True)
    include = build / "payload.nsh"
    include.write_text(payload_include(package, output, marker, audited), "utf-8-sig")
    compiler_version = subprocess.check_output([str(compiler), "/VERSION"], text=True).strip()
    if compiler_version != "v3.13":
        raise ValueError("This installer build is pinned to NSIS 3.13")
    with (build / "compile.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                str(compiler),
                "/INPUTCHARSET",
                "UTF8",
                "/V3",
                f"/DPAYLOAD_INCLUDE={include}",
                str(root / "installer/YanChuan.nsi"),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=root,
        )
    report = {
        "installer": str(output),
        "bytes": output.stat().st_size,
        "payload_bytes": audited["bytes"],
        "payload_files": len(audited["files"]),
        "compiler": compiler_version,
        "signed": False,
        "credentials_scan_passed": True,
        "models_included": False,
        "configuration_included": False,
        "install_scope": "current-user",
        "user_data_retained_on_uninstall": True,
    }
    output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
