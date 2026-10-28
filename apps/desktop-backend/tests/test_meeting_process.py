import json
import os
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from livecaptions_meetings import MeetingStore
from livecaptions_service.config import MeetingSettings


def test_real_process_rename_full_copy_and_chat_events(tmp_path):
    store = MeetingStore.create(tmp_path / "data/meetings", "原会议")
    store.append("a", 3, 6, "周五完成验收。" * 2000)
    directory = store.directory
    store.close()
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = json.dumps(
                {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "周五完成。[00:00:03]"}}
                    ]
                }
            ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    MeetingSettings(f"http://127.0.0.1:{server.server_port}/v1", "test", "test-key").save(
        tmp_path / "data/meeting-settings.json"
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "livecaptions_backend", "--root", str(tmp_path)],
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

    def call(identifier, method, **params):
        process.stdin.write(
            json.dumps({"id": identifier, "method": method, "params": params}) + "\n"
        )
        process.stdin.flush()
        while True:
            message = output.get(timeout=20)
            if message["type"] == "event":
                events.append(message)
            elif message.get("id") == identifier:
                assert "error" not in message, message
                return message["result"]

    try:
        assert call(1, "hello")["protocol"] == 1
        meeting_id = call(2, "meetings.open", directory=str(directory))["metadata"]["id"]
        assert call(3, "meetings.rename", title="中文新会议")["title"] == "中文新会议"
        assert call(4, "meetings.content")["text"].count("周五完成验收。") == 2000
        call(5, "chat.configure", api_profile_id="default", context_tokens=1000000)
        request = call(6, "chat.send", question="何时验收？")
        while not any(
            e["name"] == "chat.updated" and e["payload"]["messages"][-1]["status"] == "completed"
            for e in events
        ):
            events.append(output.get(timeout=20))
        conversation = call(7, "chat.list")
        assert conversation["meeting_id"] == meeting_id
        assert conversation["messages"][-1]["request_id"] == request["request_id"]
        assert "周五完成验收。" * 2000 in received[0]["messages"][1]["content"]
        assert call(8, "snapshot")["meeting"]["metadata"]["summary_cursor"] == 0
        deleted = call(9, "meetings.delete", directory=str(directory))
        assert deleted["id"] == meeting_id and deleted["current"]
        assert not directory.exists()
        assert call(10, "snapshot")["meeting"] is None
        assert call(11, "meetings.list") == []
        assert any(event["name"] == "meeting.deleted" for event in events)
        call(12, "shutdown")
        assert process.wait(timeout=15) == 0
        assert "test-key" not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        server.shutdown()
        server.server_close()
