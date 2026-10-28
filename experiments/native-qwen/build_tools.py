"""Locate local Visual Studio C++ build tools without a personal installation path."""

import os
import subprocess
from pathlib import Path


def visual_studio_root(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    if configured := os.environ.get("VSINSTALLDIR"):
        return Path(configured).resolve()
    program_files = os.environ.get("ProgramFiles(x86)")
    if not program_files:
        raise RuntimeError("Use --vs-root to select Visual Studio with C++ CMake tools")
    vswhere = Path(program_files) / "Microsoft Visual Studio/Installer/vswhere.exe"
    result = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.CMake.Project",
            "-property",
            "installationPath",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise RuntimeError("Install C++ CMake tools or specify --vs-root")
    return Path(result.stdout.strip().splitlines()[0])
