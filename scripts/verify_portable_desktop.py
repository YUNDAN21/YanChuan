"""Verify a relocated bundle offline using its own Python, without opening audio devices."""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--model-store", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    package, data = args.package.resolve(), args.data_root.resolve()
    data.mkdir(parents=True, exist_ok=False)
    python = package / "backend/python/python.exe"
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "VULKAN_SDK", "CUDA_PATH"}
    }
    windows = Path(os.environ["SYSTEMROOT"])
    env.update(
        PYTHONUTF8="1",
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PATH=os.pathsep.join((str(windows / "System32"), str(windows))),
    )
    imports = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json,sys,importlib.util,onnxruntime,livecaptions_asr; "
                "assert all(importlib.util.find_spec(n) is None for n in ('torch','transformers','qwen_asr')); "
                "print(json.dumps({'python':sys.executable,'asr':livecaptions_asr.__file__,'onnx':onnxruntime.__file__}))"
            ),
        ],
        cwd=data,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    locations = json.loads(imports.stdout.splitlines()[-1])
    assert Path(locations["asr"]).is_relative_to(package)
    assert Path(locations["onnx"]).is_relative_to(package)
    process = subprocess.Popen(
        [str(python), "-u", "-m", "livecaptions_backend", "--root", str(data)],
        cwd=data,
        env=env,
        text=True,
        encoding="utf-8",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=(data / "stderr.log").open("w", encoding="utf-8"),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    messages, events, identifier = queue.Queue(), [], 0

    def read():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                messages.put({"protocol_error": True})

    threading.Thread(target=read, daemon=True).start()

    def receive(timeout=30):
        message = messages.get(timeout=timeout)
        assert not message.get("protocol_error"), "Non-JSON stdout from bundled backend"
        if message.get("type") == "event":
            events.append(message)
        return message

    def call(method, **params):
        nonlocal identifier
        identifier += 1
        request_id = identifier
        process.stdin.write(
            json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
        )
        process.stdin.flush()
        while True:
            message = receive()
            if message.get("id") == request_id and message.get("type") == "response":
                assert "error" not in message, message.get("error", {}).get("message")
                return message["result"]

    report = {
        "success": False,
        "silent": True,
        "device_opened": False,
        "runtime_locations": locations,
    }
    try:
        hello = call("hello")
        assert not hello["configuration"]["api_key"] and not hello["configuration"]["model"]
        assert call("meetings.list") == []
        assert not any(a["ready"] for a in call("models.list")["assets"])
        call(
            "models.configure",
            selected_model="qwen-0.6b",
            source="huggingface",
            directory=str(args.model_store.resolve()),
        )
        call("models.download")
        deadline = time.monotonic() + 60
        while not any(
            e.get("name") == "models.download" and e["payload"]["state"] == "completed"
            for e in events
        ):
            assert time.monotonic() < deadline
            receive()
            assert not any(
                e.get("name") == "models.download" and e["payload"]["state"] == "failed"
                for e in events
            )
        call("schedule.set", minutes=5, automatic=False)
        call(
            "session.start",
            mode="file",
            title="便携版无声验证",
            file=str(args.audio.resolve()),
            selection={"source": "system", "language": "zh"},
        )
        deadline = time.monotonic() + 300
        while not any(e.get("name") == "finished" for e in events):
            assert time.monotonic() < deadline
            receive(timeout=90)
        snapshot = call("snapshot")
        assert snapshot["state"] == "ended", snapshot["state"]
        assert snapshot["meeting"]["entries"]
        call("meetings.rename", title="便携版重定位验证成功")
        text = call("meetings.content")["text"]
        call("meetings.open", directory=snapshot["meeting"]["directory"])
        assert call("chat.list")["messages"] == []
        report.update(
            success=True,
            configuration_empty=True,
            bundled_models=False,
            cached_model_download_process=True,
            protocol_clean=True,
            entries=len(snapshot["meeting"]["entries"]),
            text=text,
            model_directory=str(args.model_store.resolve()),
        )
        call("shutdown")
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(
        "PASS: relocated standalone runtime, empty credentials, model inventory/download and real silent ASR",
        flush=True,
    )


if __name__ == "__main__":
    main()
