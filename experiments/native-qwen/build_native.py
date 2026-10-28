"""Build the pinned qwenasr.cpp checkout using workspace-only Vulkan SDK files."""

import argparse
import os
import subprocess
from pathlib import Path

from build_tools import visual_studio_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vs-root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    work = root / "work/native-qwen"
    tools = visual_studio_root(args.vs_root)
    cmake_root = tools / "Common7/IDE/CommonExtensions/Microsoft/CMake"
    cmake = cmake_root / "CMake/bin/cmake.exe"
    ninja = cmake_root / "Ninja/ninja.exe"
    sdk = work / "sdk"
    vcvars = tools / "VC/Auxiliary/Build/vcvars64.bat"
    raw = subprocess.check_output(f'cmd.exe /d /s /c "call "{vcvars}" >nul && set"', text=True)
    environment = dict(os.environ)
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            environment[key] = value
    environment["VULKAN_SDK"] = str(sdk)
    environment["PATH"] = str(sdk / "Bin") + os.pathsep + environment["PATH"]
    commands = [
        [
            str(cmake),
            "-S",
            str(work / "qwenasr.cpp"),
            "-B",
            str(work / "build-vulkan"),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DGGML_VULKAN=ON",
            "-DGGML_CUDA=OFF",
            "-DGGML_NATIVE=OFF",
            "-DQWENASR_SHARED=ON",
            "-DCMAKE_MAKE_PROGRAM=" + str(ninja),
            "-DCMAKE_PREFIX_PATH=" + str(sdk),
            "-DVulkan_INCLUDE_DIR=" + str(sdk / "Include"),
            "-DVulkan_LIBRARY=" + str(sdk / "Lib/vulkan-1.lib"),
            "-DVulkan_GLSLC_EXECUTABLE=" + str(sdk / "Bin/glslc.exe"),
        ],
        [
            str(cmake),
            "--build",
            str(work / "build-vulkan"),
            "--target",
            "qwenasr",
            "qwenasr-transcribe",
            "-j",
            "4",
        ],
    ]
    for name, command in zip(("configure", "build"), commands, strict=True):
        log = work / (name + ".log")
        print(name + ": " + str(log), flush=True)
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(
                command,
                cwd=root,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
    print("Native Vulkan shared library and CLI built successfully", flush=True)


if __name__ == "__main__":
    main()
