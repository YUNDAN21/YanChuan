"""Silently verify the actual installer in a workspace-owned directory; never opens capture/UI."""

import argparse
import ctypes
import json
import os
import subprocess
import time
import uuid
import winreg
from pathlib import Path

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LiveCaptions"
PRODUCT_KEY = r"Software\LiveCaptions"


def registry_value(key, name):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None


def run(executable, *args, expected=0):
    # NSIS parses /D= from the raw command line: its final path must not be quoted.
    command = (
        subprocess.list2cmdline([str(executable), *args[:-1]]) + " " + args[-1]
        if args and args[-1].startswith("/D=")
        else [str(executable), *args]
    )
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == expected, f"Exit {result.returncode}, expected {expected}"
    return result


def check_payload(package, installation):
    for path in package.rglob("*"):
        if path.is_file():
            installed = installation / path.relative_to(package)
            assert installed.is_file(), installed
            assert installed.stat().st_size == path.stat().st_size, installed


def shell_folder(name):
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
    ) as key:
        return Path(os.path.expandvars(winreg.QueryValueEx(key, name)[0]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    assert output.is_relative_to((root / "data/verification").resolve())
    installation = output / "安装 测试"
    menu, desktop = (
        shell_folder("Programs") / "言川",
        shell_folder("Desktop") / "言川.lnk",
    )
    if (
        registry_value(PRODUCT_KEY, "InstallDir")
        or registry_value(UNINSTALL_KEY, "InstallLocation")
        or menu.exists()
        or desktop.exists()
    ):
        raise RuntimeError(
            "Existing installation/shortcuts found; isolated installer verification requires an unused application identity"
        )
    output.mkdir(parents=True, exist_ok=False)
    sentinel = (
        Path(os.environ["LOCALAPPDATA"])
        / "LiveCaptions/data"
        / f"installer-test-{uuid.uuid4().hex}.json"
    )
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"keep_user_data":true}', "utf-8")
    report = {"success": False, "audio_played": False, "devices_opened": False}
    try:
        run(args.installer.resolve(), "/S", f"/D={installation}")
        check_payload(args.package.resolve(), installation)
        assert registry_value(UNINSTALL_KEY, "InstallLocation") == str(installation)
        assert (menu / "言川.lnk").is_file() and (menu / "卸载言川.lnk").is_file()
        assert not desktop.exists()  # optional checkbox defaults off
        report["install_and_shortcuts"] = True
        keep = installation / "models/user-model-fixture.txt"
        keep.parent.mkdir()
        keep.write_text("user model directory must survive", "utf-8")
        # Same-version reinstall exercises the upgrade path with retained user data.
        run(args.installer.resolve(), "/S", f"/D={installation}")
        check_payload(args.package.resolve(), installation)
        assert keep.read_text("utf-8") == "user model directory must survive"
        report["reinstall"] = True
        # Lock a runtime file to simulate an active process, without launching the UI or model.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(
            str(installation / "YanChuan.exe"), 0x80000000, 1, None, 3, 0, None
        )
        assert handle != ctypes.c_void_p(-1).value
        try:
            run(args.installer.resolve(), "/S", f"/D={installation}", expected=2)
        finally:
            kernel.CloseHandle(handle)
        report["running_app_not_overwritten"] = True
        python = installation / "backend/python/python.exe"
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": os.pathsep.join(
                (str(Path(os.environ["SYSTEMROOT"]) / "System32"), os.environ["SYSTEMROOT"])
            ),
        }
        for key in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "VULKAN_SDK", "CUDA_PATH"):
            env.pop(key, None)
        result = subprocess.run(
            [
                str(python),
                "-u",
                "-m",
                "livecaptions_backend",
                "--root",
                str(output / "isolated-data"),
            ],
            input='{"id":1,"method":"hello"}\n{"id":2,"method":"shutdown"}\n',
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        hello = next(item["result"] for item in messages if item.get("id") == 1)
        assert not hello["snapshot"]["active"] and not hello["configuration"]["api_key"]
        report["installed_backend_handshake"] = True
        result = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import sys;from pathlib import Path;"
                    "from livecaptions_asr.engines.native_binding import NativeBinding;"
                    "assert NativeBinding(Path(sys.argv[1])).lib.whisper_version()==b'0.8.45'"
                ),
                str(installation / "backend/native/crispasr.dll"),
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0
        report["installed_native_library"] = True
        uninstaller = installation / "Uninstall.exe"
        assert uninstaller.resolve().is_relative_to(output)
        run(uninstaller, "/S")
        deadline = time.monotonic() + 60
        while (
            (installation / "YanChuan.exe").exists()
            or (installation / "backend").exists()
            or (installation / "Uninstall.exe").exists()
            or registry_value(PRODUCT_KEY, "InstallDir")
        ):
            assert time.monotonic() < deadline, "Uninstall did not finish"
            time.sleep(0.2)
        assert not (installation / "backend").exists()
        assert not menu.exists() and not desktop.exists()
        assert registry_value(UNINSTALL_KEY, "InstallLocation") is None
        assert sentinel.read_text("utf-8") == '{"keep_user_data":true}'
        assert keep.read_text("utf-8") == "user model directory must survive"
        report.update(
            success=True, uninstall=True, user_data_retained=True, unknown_model_files_retained=True
        )
    finally:
        sentinel.unlink(missing_ok=True)
        (output / "report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
