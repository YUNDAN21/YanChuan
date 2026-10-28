import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from livecaptions_meetings import (
    SUMMARY_SYSTEM_PROMPT,
    ChatSummaryClient,
    MeetingStore,
    SummaryError,
    SummarySettings,
    SummaryWorker,
    list_meetings,
    timestamp,
)


def wait_for(condition):
    deadline = monotonic() + 4
    while not condition():
        assert monotonic() < deadline, "meeting worker timeout"
        sleep(0.01)


def test_store_durable_transcripts_dedupe_exports_and_summary_checkpoint(tmp_path):
    store = MeetingStore.create(tmp_path, "周会")
    first = store.append("s:0", 1.25, 3.5, "确认上线计划。")
    assert store.append("s:0", 1.25, 3.5, "重复事件") is None
    second = store.append("s:1", 11, 16, "小李周五交付测试。")
    summary = store.save_summary([first], "- [00:00:01] 确认上线计划。", "test")
    assert summary.first_entry == first.id and summary.last_entry == first.id
    directory = store.directory
    store.close()
    reopened = MeetingStore(directory)
    assert reopened.metadata["summary_cursor"] == first.id
    assert reopened.pending_batch(second.id) == [second]
    assert len(reopened.summaries()) == 1
    assert list_meetings(tmp_path)[0]["title"] == "周会"
    assert "[00:00:11–00:00:16] 小李周五交付测试。" in (directory / "transcript.md").read_text(
        encoding="utf-8"
    )
    with pytest.raises(ValueError, match="进度"):
        reopened.save_summary([first, second], "重复范围", "test")
    reopened.set_status("ended", ended=True)
    assert json.loads((directory / "meeting.json").read_text(encoding="utf-8"))["ended_at"]
    reopened.close()


def test_store_validates_time_and_bounds_summary_input(tmp_path):
    store = MeetingStore.create(tmp_path, "test")
    for times in [(2, 1), (-1, 2), (0, float("inf"))]:
        with pytest.raises(ValueError):
            store.append("invalid", *times, "text")
    assert store.append("empty", 0, 1, " ") is None
    entries = [store.append(str(i), i, i + 1, "一句话" * 8) for i in range(6)]
    batch = store.pending_batch(store.last_entry, max_chars=120)
    assert 0 < len(batch) < len(entries)
    assert sum(len(item.markdown()) for item in batch) <= 120
    assert timestamp(3662.9) == "01:01:02"
    store.close()


def test_interval_does_not_summarize_silence_and_end_drains_tail(tmp_path):
    store = MeetingStore.create(tmp_path, "test")
    now, calls = [0.0], []
    client = SimpleNamespace(
        model="test",
        summarize=lambda title, entries, previous: calls.append([e.id for e in entries]) or "总结",
    )
    worker = SummaryWorker(store, client, interval_seconds=60, clock=lambda: now[0])
    worker.start()
    try:
        now[0] = 61
        worker.tick()
        assert not worker.request()
        entry = store.append("0", 0, 4, "新的讨论")
        assert not calls
        now[0] = 122
        worker.tick()
        wait_for(lambda: store.metadata["summary_cursor"] == entry.id)
        tail = store.append("1", 65, 70, "结束前的待办")
        worker.close(drain=True)
        worker.wait(3)
        assert not worker.running
        assert calls == [[entry.id], [tail.id]]
    finally:
        worker.close(drain=False)
        worker.wait(3)
        store.close()


def test_slow_summary_does_not_block_ingest_and_coalesces_requests(tmp_path):
    store = MeetingStore.create(tmp_path, "test")
    store.append("0", 0, 1, "第一段")
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Client:
        model = "test"

        def summarize(self, title, entries, previous):
            calls.append([entry.id for entry in entries])
            entered.set()
            assert release.wait(3)
            return "阶段总结"

    worker = SummaryWorker(store, Client(), automatic=False)
    worker.start()
    try:
        worker.request()
        assert entered.wait(2)
        second = store.append("1", 3, 4, "模型仍在总结时继续写入")
        worker.request()
        worker.request()
        release.set()
        wait_for(lambda: store.metadata["summary_cursor"] == second.id)
        assert calls == [[1], [2]]
        assert len(store.entries()) == 2
    finally:
        release.set()
        worker.close(drain=False)
        worker.wait(3)
        store.close()


def test_failure_does_not_advance_cursor_and_can_retry_after_reopen(tmp_path):
    store = MeetingStore.create(tmp_path, "test")
    store.append("0", 0, 1, "必须保留")
    updates = []

    class Failing:
        model = "test"
        settings = SimpleNamespace(api_key="private-token")

        def summarize(self, *args):
            raise SummaryError("service failed: private-token")

    worker = SummaryWorker(
        store,
        Failing(),
        automatic=False,
        on_update=lambda kind, value: updates.append((kind, value)),
    )
    worker.start()
    worker.request()
    wait_for(lambda: bool(worker.last_error))
    worker.close(drain=False)
    worker.wait(2)
    assert store.metadata["summary_cursor"] == 0 and len(store.entries()) == 1
    assert all("private-token" not in str(value) for _, value in updates)
    path = store.directory
    store.close()
    reopened = MeetingStore(path)
    retry = SummaryWorker(
        reopened, SimpleNamespace(model="test", summarize=lambda *args: "恢复成功"), automatic=False
    )
    retry.start()
    retry.close(drain=True)
    retry.wait(3)
    assert reopened.metadata["summary_cursor"] == 1
    assert reopened.summaries()[0].text == "恢复成功"
    reopened.close()


@pytest.mark.parametrize("prompt", [None, "请用三段中文概括，保留关键数字。"])
def test_summary_client_uses_requested_model_and_preserves_timestamps(tmp_path, prompt):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["Authorization"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            body = json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": "- [00:00:03] 确认&#x20;计划。 x &lt; 2 &amp; y"
                            },
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = MeetingStore.create(tmp_path, "协议测试")
    entry = store.append("0", 3, 5, "确认计划。")
    try:
        client = ChatSummaryClient(
            SummarySettings(
                f"http://127.0.0.1:{server.server_port}/v1",
                "requested-model",
                "test-token",
                **({"system_prompt": prompt} if prompt is not None else {}),
            )
        )
        assert client.summarize("协议测试", [entry]) == "- [00:00:03] 确认 计划。 x &lt; 2 &amp; y"
        path, authorization, body = received[0]
        assert path == "/v1/chat/completions" and authorization == "Bearer test-token"
        assert body["model"] == "requested-model" and not body["stream"]
        assert body["messages"][0] == {
            "role": "system",
            "content": SUMMARY_SYSTEM_PROMPT if prompt is None else prompt,
        }
        assert "00:00:03" in body["messages"][1]["content"]
        assert "test-token" not in repr(client.settings)
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_client_change_keeps_current_task_consistent_and_applies_to_next_task(tmp_path):
    store = MeetingStore.create(tmp_path, "切换提示词")
    store.append("a", 0, 1, "第一段内容")
    store.append("b", 2, 3, "第二段内容")
    entered, release = threading.Event(), threading.Event()
    calls = []

    class OldClient:
        model = "old"

        def summarize(self, title, entries, previous):
            calls.append((self.model, [entry.id for entry in entries]))
            entered.set()
            assert release.wait(3)
            return "旧提示词的结果"

    def new_summary(title, entries, previous):
        calls.append(("new", [entry.id for entry in entries]))
        return "新提示词的结果"

    worker = SummaryWorker(store, OldClient(), automatic=False, max_chars=30)
    worker.start()
    try:
        worker.request()
        assert entered.wait(2)
        worker.set_client(SimpleNamespace(model="new", summarize=new_summary))
        store.append("c", 4, 5, "第三段内容")
        worker.request()
        release.set()
        wait_for(lambda: store.metadata["summary_cursor"] == 3)
        assert calls == [("old", [1]), ("old", [2]), ("new", [3])]
        assert [summary.model for summary in store.summaries()] == ["old", "old", "new"]
    finally:
        release.set()
        worker.close(drain=False)
        worker.wait(3)
        store.close()
