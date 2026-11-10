"""Real JSONL pipes with an injected runtime; no models or recording devices."""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


def test_source_switch_jsonl_events_and_session_ownership(tmp_path):
    fixtures = Path(__file__).resolve().parents[3] / "packages/desktop-service/tests"
    script = """
import sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from test_source_switch import SwitchingRuntime
from livecaptions_service import DesktopService
from livecaptions_backend.__main__ import serve
class Runtime(SwitchingRuntime):
    def set_source(self, source):
        future = super().set_source(source)
        threading.Timer(0.03, lambda: future.set_result(source)).start()
        return future
serve(sys.stdin, sys.stdout, Path(sys.argv[1]),
      service_factory=lambda root, observer: DesktopService(root, observer=observer, runtime=Runtime()))
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(tmp_path), str(fixtures)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    output, events = queue.Queue(), []

    def read():
        for line in process.stdout:
            output.put(json.loads(line))

    threading.Thread(target=read, daemon=True).start()
    identifier = 0

    def receive():
        item = output.get(timeout=10)
        if item.get("type") == "event":
            events.append(item)
        return item

    def call(method, **params):
        nonlocal identifier
        identifier += 1
        process.stdin.write(
            json.dumps({"id": identifier, "method": method, "params": params}) + "\n"
        )
        process.stdin.flush()
        while True:
            item = receive()
            if item.get("id") == identifier:
                return item

    try:
        hello = call("hello")["result"]
        assert hello["snapshot"]["audio_source"]["source"] == "both"
        session_id = call("session.start", title="音源切换测试", selection={"source": "system"})[
            "result"
        ]["session_id"]
        while not any(
            e["name"] == "state" and e["payload"]["state"] == "recording" for e in events
        ):
            receive()
        for source in ("microphone", "both", "system"):
            after = max(e["seq"] for e in events)
            response = call("session.source", source=source, session_id=session_id)
            assert "error" not in response
            while not any(
                e["seq"] > after
                and e["name"] == "session.source"
                and e["payload"]["source"] == source
                and not e["payload"]["switching"]
                for e in events
            ):
                receive()
            snapshot = call("snapshot")["result"]
            assert snapshot["audio_source"]["source"] == source and snapshot["active"]
        stale = call("session.source", source="both", session_id="old-session")
        assert "会议已切换" in stale["error"]["message"]
        call("session.stop")
        while not any(e["name"] == "finished" for e in events):
            receive()
        snapshot = call("snapshot")["result"]
        assert not snapshot["active"]
        assert snapshot["meeting"]["entries"][0]["text"] == "测试完成。"
        assert snapshot["session_id"] == session_id
        assert (
            "录音过程中"
            in call("session.source", source="both", session_id=session_id)["error"]["message"]
        )
        assert all(e["session_id"] == session_id for e in events)
        assert [e["seq"] for e in events] == sorted({e["seq"] for e in events})
        call("shutdown")
        assert process.wait(10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(10)
