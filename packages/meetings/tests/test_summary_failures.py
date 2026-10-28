import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from livecaptions_meetings import (
    ChatSummaryClient,
    MeetingStore,
    SummarySettings,
    SummaryWorker,
)


@pytest.mark.parametrize(
    "status,choice",
    [
        (503, {}),
        (200, {"finish_reason": "length", "message": {"content": "incomplete"}}),
        (200, {"finish_reason": "stop", "message": {"content": ""}}),
    ],
)
def test_failed_or_truncated_responses_preserve_summary_checkpoint(tmp_path, status, choice):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [choice]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store = MeetingStore.create(tmp_path, "gateway failures")
    entry = store.append("0", 0, 3, "不能丢失的转写")
    client = ChatSummaryClient(
        SummarySettings(f"http://127.0.0.1:{server.server_port}/v1", "test", "test-secret")
    )
    worker = SummaryWorker(store, client, automatic=False)
    worker.start()
    try:
        worker.close(drain=True)
        worker.wait(3)
        assert not worker.running
        assert worker.last_error
        assert store.metadata["summary_cursor"] == 0
        assert store.entries() == [entry]
        assert len(store.summary_errors()) == 1
        assert not store.summaries()
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_long_backlog_is_processed_in_bounded_nonoverlapping_requests(tmp_path):
    store = MeetingStore.create(tmp_path, "long backlog")
    for index in range(8):
        store.append(str(index), index * 4, index * 4 + 3, "待总结内容" * 6)
    calls = []

    def summarize(title, entries, previous):
        calls.append([entry.id for entry in entries])
        assert sum(len(entry.markdown()) for entry in entries) <= 100
        return "已总结当前小段"

    worker = SummaryWorker(
        store, SimpleNamespace(model="test", summarize=summarize), automatic=False, max_chars=100
    )
    worker.start()
    worker.close(drain=True)
    worker.wait(3)
    assert not worker.running
    assert len(calls) > 1
    assert [number for batch in calls for number in batch] == list(range(1, 9))
    assert store.metadata["summary_cursor"] == store.last_entry
    store.close()
