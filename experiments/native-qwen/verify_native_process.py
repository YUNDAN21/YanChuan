"""Exercise the shipped native child protocol under an isolated Windows PATH."""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


class Child:
    def __init__(self, python, library, data, audio):
        env = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "VULKAN_SDK", "CUDA_PATH"):
            env.pop(name, None)
        windows = Path(os.environ["SYSTEMROOT"])
        env.update(
            PATH=os.pathsep.join((str(windows / "System32"), str(windows))),
            PYTHONUTF8="1",
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.stderr = (data / "process.stderr.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                str(python),
                "-u",
                "-m",
                "livecaptions_backend",
                "--root",
                str(data),
                "--native-library",
                str(library),
                "--verify-pcm",
                str(audio),
            ],
            cwd=data,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.messages, self.events, self.serial = queue.Queue(), [], 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                self.messages.put({"invalid_json": True})
        self.messages.put({"eof": True})

    def next(self, timeout=120):
        item = self.messages.get(timeout=timeout)
        assert not item.get("invalid_json"), "DLL/stdout corrupted JSONL"
        if item.get("type") == "event":
            self.events.append(item)
        return item

    def call(self, method, **params):
        self.serial += 1
        identifier = self.serial
        self.process.stdin.write(
            json.dumps({"id": identifier, "method": method, "params": params}, ensure_ascii=False)
            + "\n"
        )
        self.process.stdin.flush()
        while True:
            item = self.next()
            assert not item.get("eof"), "Child disconnected"
            if item.get("type") == "response" and item.get("id") == identifier:
                assert "error" not in item, item.get("error")
                return item["result"]

    def wait_event(self, name, start=0, predicate=lambda payload: True):
        deadline = time.monotonic() + 180
        while True:
            for event in self.events[start:]:
                if event["name"] == name and predicate(event["payload"]):
                    return event
            assert time.monotonic() < deadline, name
            assert not self.next().get("eof"), "Child disconnected"

    def close(self):
        if self.process.poll() is None:
            self.call("shutdown")
            self.process.stdin.close()
            assert self.process.wait(timeout=120) == 0
        self.reader.join(3)
        self.stderr.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    package, data = args.package.resolve(), args.output.resolve()
    data.mkdir(parents=True, exist_ok=False)
    python = package / "backend/python/python.exe"
    library = package / "backend/native/crispasr.dll"
    audio = root / "data/samples/asr_zh.wav"
    report = {"complete": False, "playback": False, "device_opened": False}
    child = Child(python, library, data, audio)
    try:
        hello = child.call("hello")
        assert hello["configuration"]["api_key"] == ""
        assert not hello["snapshot"]["active"]
        assert child.call("meetings.list") == []
        child.call(
            "models.configure",
            selected_model="qwen-0.6b",
            source="huggingface",
            directory=str(root / "models"),
        )
        child.call("schedule.set", minutes=5, automatic=False)
        child.call("session.start", mode="file", file=str(audio), title="加载中取消")
        child.wait_event(
            "progress",
            predicate=lambda p: p["stage"] == "native_weights" and p["state"] == "started",
        )
        started = time.monotonic()
        child.call("session.stop")
        report["stop_response_seconds"] = time.monotonic() - started
        assert report["stop_response_seconds"] < 3
        child.call("session.stop")
        child.wait_event("finished")
        snapshot = child.call("snapshot")
        assert not snapshot["active"] and not snapshot["meeting"]["entries"]
        cancelled = snapshot["meeting"]["directory"]
        for mode in ("file", "meeting"):
            index = len(child.events)
            child.call(
                "session.start",
                mode=mode,
                title="原生协议验证 中文",
                **({"file": str(audio)} if mode == "file" else {}),
                selection={"source": "system", "language": "zh"},
            )
            child.wait_event("finished", start=index)
            snapshot = child.call("snapshot")
            assert snapshot["state"] == "ended" and snapshot["meeting"]["entries"]
            child.call("meetings.rename", title="已改名 · 原生验证")
            text = child.call("meetings.content")["text"]
            assert "已改名" in text
            child.call("meetings.open", directory=snapshot["meeting"]["directory"])
            assert child.call("snapshot")["meeting"]["entries"] == snapshot["meeting"]["entries"]
        loads = [
            e
            for e in child.events
            if e["name"] == "progress"
            and e["payload"]["stage"] == "native_weights"
            and e["payload"]["state"] == "completed"
        ]
        assert len(loads) == 1
        child.call("meetings.open", directory=cancelled)
        assert child.call("snapshot")["meeting"]["metadata"]["status"] == "ended"
        report.update(
            model_loads=len(loads),
            event_count=len(child.events),
            protocol_clean=True,
            file_and_pcm_meeting=True,
            history_reopened=True,
            load_stop_received=True,
        )
        child.close()
        # EOF and reconnect preserve saved data and never start recording.
        child = Child(python, library, data, audio)
        assert not child.call("hello")["snapshot"]["active"]
        assert len(child.call("meetings.list")) == 3
        child.process.kill()  # this test-owned child only
        child.process.wait(timeout=15)
        assert child.next(timeout=10).get("eof")
        child.close()
        child = Child(python, library, data, audio)
        assert not child.call("hello")["snapshot"]["active"]
        assert len(child.call("meetings.list")) == 3
        report["complete"] = True
    finally:
        child.close()
        (data / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
