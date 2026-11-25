import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livecaptions_meetings import (
    AGENT_PROMPT,
    ChatCoordinator,
    MeetingAgent,
    MeetingReference,
    MeetingStore,
    SummarySettings,
    TranscriptEntry,
    create_summary_client,
)
from livecaptions_meetings.chat import build_chat_messages
from livecaptions_meetings.chat_context import text_tokens
from livecaptions_meetings.meeting_agent import AgentError
from test_chat import wait
from test_protocols import completed, server_response


class Scripted:
    model = "fixture"

    def __init__(self, replies):
        self.replies, self.calls = iter(replies), []

    def chat(self, messages):
        payload = json.loads(messages[-1]["content"].split("查阅任务（JSON）：\n")[-1])
        self.calls.append((messages, payload))
        value = next(self.replies)
        return json.dumps(value(payload) if callable(value) else value, ensure_ascii=False)


def reference(count=3):
    return MeetingReference(
        "meeting",
        "讨论",
        [
            TranscriptEntry(i + 1, str(i), i * 60, i * 60 + 20, f"第{i}段，发布日期周五。")
            for i in range(count)
        ],
    )


def run(agent, question="何时发布？", cancel=None):
    events = []
    text = agent.answer(
        build_chat_messages(agent.reference.overview(), [], question),
        cancel or threading.Event(),
        events.append,
    )
    return text, events


def test_reference_paging_unicode_search_and_boundary_preserve_exact_original():
    text = "甲" * 598 + "部署日期" + "乙" * 700
    source = MeetingReference(
        "m",
        "title",
        [
            TranscriptEntry(1, "a", 0, 20, text),
            TranscriptEntry(2, "b", 30, 40, "ＦＯＯ foo 日本語"),
        ],
    )
    assert "".join(c.text for c in source.chunks if c.entry_id == 1) == text
    assert source.search(["部署日期"])["items"][0]["id"] == 0
    assert source.search(["FOO"])["items"][0]["entry_id"] == 2
    assert source.search(["日本語"])["matches"] == 1
    assert source.read(0)["next"] == 1
    assert len(source.directory()["items"][0]["text"]) <= 80
    assert source.search(["不存在"])["matches"] == 0
    for call in [
        lambda: source.read(-1),
        lambda: source.read(True),
        lambda: source.search("text"),
        lambda: source.search(["a"] * 9),
        lambda: source.read(0, 1000),
    ]:
        with pytest.raises(ValueError):
            call()


def test_targeted_search_reread_and_verify_only_send_retrieved_text():
    source = reference(80)
    client = Scripted(
        [
            {"mode": "targeted"},
            {"action": "search", "terms": ["发布日期"], "notes": "寻找日期"},
            {"action": "read", "cursor": 70, "notes": "确认后续更新"},
            {"action": "read", "cursor": 71, "notes": "已读70，继续核对71"},
            {"action": "answer", "text": "周五发布。", "citations": [71]},
            {"text": "最终确定周五发布。", "citations": [71]},
        ]
    )
    result, events = run(MeetingAgent(source, client, context_tokens=8192, full_context=False))
    assert result.endswith("来源：[01:11:00–01:11:20]")
    assert client.calls[-1][1]["phase"] == "verify"
    assert "第70段" not in str(client.calls[-2][1]["tool_result"])
    assert "第71段" in str(client.calls[-2][1]["tool_result"])
    assert "第79段" not in str(client.calls)
    assert "正在搜索会议原文…" in events
    for messages, _ in client.calls:
        assert (
            text_tokens(AGENT_PROMPT) + sum(text_tokens(m["content"]) + 12 for m in messages) + 4096
            <= 8192
        )


def test_global_scan_covers_every_chunk_and_rechecks_citations():
    source = reference(9)
    replies = [{"mode": "global"}]
    replies += [
        lambda p: {
            "findings": [{"text": "发布时间确认", "citations": [p["source"]["items"][0]["id"]]}]
        }
        for _ in source.chunks
    ]
    replies += [{"text": "各时段均确认周五。", "citations": [0, 8]}]
    replies += [{"text": "各时段均确认周五。", "citations": [0, 8]}]
    client = Scripted(replies)
    text, progress = run(
        MeetingAgent(source, client, context_tokens=8192, full_context=False), "整场所有决定"
    )
    extracts = [p for _, p in client.calls if p["phase"] == "extract"]
    assert [p["source"]["items"][0]["id"] for p in extracts] == list(range(9))
    final = next(p for _, p in client.calls if p["phase"] == "final")
    assert final["coverage"] == {"read": 9, "total": 9}
    assert "9/9" in str(progress) and "[00:08:00–00:08:20]" in text


def test_scan_never_silently_skips_batches_at_call_limit():
    client = Scripted([{"mode": "global"}, {"findings": []}])
    with pytest.raises(AgentError, match="请求上限"):
        run(MeetingAgent(reference(3), client, max_calls=2, full_context=False))
    assert not any(p["phase"] == "final" for _, p in client.calls)


@pytest.mark.parametrize(
    "reply",
    [
        {"action": "answer", "text": "编造", "citations": [999]},
        {"action": "answer", "text": "没读原文", "citations": []},
    ],
)
def test_unread_or_invalid_citations_never_become_an_answer(reply):
    with pytest.raises(AgentError):
        run(MeetingAgent(reference(), Scripted([{"mode": "targeted"}, reply]), full_context=False))


def test_bad_tool_arguments_recover_and_unknown_actions_cannot_execute():
    client = Scripted(
        [
            {"mode": "targeted"},
            {"action": "shell", "command": "anything"},
            {"action": "read", "cursor": "../file"},
            {"action": "read", "cursor": 0},
            {"action": "answer", "text": "已核对", "citations": [0]},
            {"text": "已核对", "citations": [0]},
        ]
    )
    result, _ = run(MeetingAgent(reference(), client, full_context=False))
    assert "已核对" in result
    assert "未知指令" in str(client.calls[2])
    assert "参数无效" in str(client.calls[3])


def test_snapshot_remains_stable_after_append_and_model_cancel_stops_next_call(tmp_path):
    store = MeetingStore.create(tmp_path, "snapshot")
    store.append("a", 0, 1, "原始内容")
    source = store.reference_snapshot()
    assert source is store.reference_snapshot()
    store.append("b", 1, 2, "后来追加")
    assert len(source.chunks) == 1 and len(store.reference_snapshot().chunks) == 2
    coordinator, entered, release = ChatCoordinator(), threading.Event(), threading.Event()

    class Blocking:
        model = "test"
        calls = 0

        def chat(self, messages):
            self.calls += 1
            entered.set()
            assert release.wait(3)
            return '{"mode":"global"}'

    client = Blocking()
    agent = MeetingAgent(source, client, full_context=False)
    try:
        sent = coordinator.send(
            store.directory,
            {
                "meeting_id": source.meeting_id,
                "through_entry": source.through_entry,
                "content": source.overview(),
            },
            "总结",
            client,
            answer=agent.answer,
        )
        assert entered.wait(1)
        assert coordinator.cancel(source.meeting_id, sent["request_id"])
        release.set()
        wait(lambda: not coordinator._workers)
        assert client.calls == 1
        assert coordinator.list(store.directory)[-1].status == "cancelled"
    finally:
        release.set()
        coordinator.close()
        store.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
def test_portable_action_contract_over_real_http(protocol):
    # A tool result comes back as ordinary JSON text, using each provider's existing wire.
    with server_response(completed(protocol, '{"mode":"global"}')) as (base, received):
        client = create_summary_client(
            SummarySettings(
                base,
                "model",
                "fixture",
                protocol=protocol,
                system_prompt=AGENT_PROMPT,
            )
        )
        agent = MeetingAgent(reference(1), client, max_calls=1, full_context=False)
        with pytest.raises(AgentError, match="请求上限"):
            run(agent)
        body = received[0][2]
        prompt = (
            body["instructions"]
            if protocol == "responses"
            else (body["system"] if protocol == "anthropic" else body["messages"][0]["content"])
        )
        assert prompt == AGENT_PROMPT
        assert "phase" in str(body)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
@pytest.mark.parametrize("mode", ["global", "targeted"])
def test_complete_agent_wire_loop_for_all_protocols(protocol, mode):
    phases = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            turns = body["input"] if protocol == "responses" else body["messages"]
            task = json.loads(turns[-1]["content"].split("查阅任务（JSON）：\n")[-1])
            phase = task["phase"]
            phases.append(phase)
            if phase == "route":
                answer = {"mode": mode}
            elif phase == "research":
                answer = (
                    {"action": "read", "cursor": 0}
                    if phases.count("research") == 1
                    else {"action": "answer", "text": "周五发布。", "citations": [0]}
                )
            elif phase == "extract":
                answer = {"findings": [{"text": "周五发布。", "citations": [0]}]}
            else:
                answer = {"text": "周五发布。", "citations": [0]}
            raw = json.dumps(completed(protocol, json.dumps(answer, ensure_ascii=False))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = create_summary_client(
            SummarySettings(
                f"http://127.0.0.1:{server.server_port}/v1",
                "test",
                "fixture-key",
                protocol=protocol,
                system_prompt=AGENT_PROMPT,
            )
        )
        text, _ = run(MeetingAgent(reference(2), client, full_context=False))
        assert text.startswith("周五发布") and "来源：[00:00:00" in text
        assert phases == (
            ["route", "extract", "final", "verify"]
            if mode == "global"
            else ["route", "research", "research", "verify"]
        )
    finally:
        server.shutdown()
        server.server_close()


def test_existing_context_database_ignores_removed_range_columns(tmp_path):
    from livecaptions_meetings import ChatHistory

    store = MeetingStore.create(tmp_path, "旧配置")
    history = ChatHistory(store.directory)
    with history._db:
        history._db.execute("ALTER TABLE chat_context ADD COLUMN range_start REAL")
        history._db.execute("ALTER TABLE chat_context ADD COLUMN range_end REAL")
        history._db.execute("UPDATE chat_context SET range_start=60,range_end=120")
    history.close()
    reopened = ChatHistory(store.directory)
    try:
        assert "range_start" not in reopened.context.snapshot()
        assert "range_end" not in reopened.context.snapshot()
    finally:
        reopened.close()
        store.close()
