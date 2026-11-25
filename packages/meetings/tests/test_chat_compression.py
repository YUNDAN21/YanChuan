import json
import threading
from types import SimpleNamespace

import pytest
from livecaptions_meetings import (
    COMPACTION_PROMPT,
    ChatCoordinator,
    ChatHistory,
    MeetingStore,
    SummarySettings,
    chat_context_usage,
    create_summary_client,
)
from livecaptions_meetings.chat import build_chat_messages
from livecaptions_meetings.chat_context import compress_messages, text_tokens
from test_chat import Client, wait
from test_protocols import completed, server_response


def seed(directory, count=5, start=0):
    history = ChatHistory(directory)
    try:
        for index in range(start, start + count):
            history.begin(f"r{index}", f"问题{index}：" + "保留用户需求。" * 40, "fixture", 0)
            history.finish(f"r{index}", "completed", text=f"回答{index}：" + "原始讨论细节。" * 40)
        return history.messages()
    finally:
        history.close()


class Compressor:
    model = "chat-selected"

    def __init__(
        self, *, fail=False, block=False, text="早期对话：用户要求周五交付，并保留时间戳。"
    ):
        self.calls, self.fail, self.text = [], fail, text
        self.entered, self.release = threading.Event(), threading.Event()
        if not block:
            self.release.set()

    def chat(self, messages):
        self.calls.append(messages)
        self.entered.set()
        assert self.release.wait(5)
        if self.fail:
            raise RuntimeError("secret-provider-response")
        return self.text


def test_compress_retains_recent_turns_full_meeting_and_original_messages(tmp_path):
    store = MeetingStore.create(tmp_path, "压缩")
    store.append("meeting", 0, 3, "MEETING-ONLY 原文保持完整。" * 200)
    original = seed(store.directory)
    coordinator, compressor = ChatCoordinator(), Compressor()
    snapshot = store.context_snapshot()
    before = chat_context_usage(snapshot["content"], original)
    try:
        coordinator.compress(store.directory, store.metadata["id"], compressor)
        wait(lambda: coordinator.context(store.directory)["status"] == "completed")
        memory = coordinator.context(store.directory)
        assert memory["through_message"] == original[-5].id  # final assistant of third turn
        assert coordinator.list(store.directory) == original
        assert store.metadata["summary_cursor"] == 0
        assert all("MEETING-ONLY" not in json.dumps(call) for call in compressor.calls)
        usage = chat_context_usage(snapshot["content"], original, memory=memory)
        assert usage["total_tokens"] < before["total_tokens"]
        assert usage["meeting_tokens"] == before["meeting_tokens"]
        messages = build_chat_messages(snapshot["content"], original, "继续", memory=memory)
        assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
        assert (
            snapshot["content"]
            in json.loads(messages[0]["content"].split("\n\n用户问题：")[0])["meeting_reference"]
        )
        assert "早期对话" in messages[0]["content"]
        assert "问题0：" not in "".join(m["content"] for m in messages)
        client = Client()
        sent = coordinator.send(store.directory, snapshot, "继续", client)
        assert (
            sent["estimated_tokens"]
            == chat_context_usage(snapshot["content"], original, "继续", memory=memory)[
                "estimated_tokens"
            ]
        )
        wait(lambda: coordinator.list(store.directory)[-1].status == "completed")
        restarted = ChatCoordinator()
        assert restarted.context(store.directory)["summary"] == memory["summary"]
        restarted.configure_context(store.directory, store.metadata["id"], reset=True)
        assert not restarted.context(store.directory)["summary"]
        assert len(restarted.list(store.directory)) == len(original) + 2
        restarted.close()
    finally:
        coordinator.close()
        store.close()


@pytest.mark.parametrize("failure", ["network", "empty", "long"])
def test_failed_compaction_keeps_previous_checkpoint_and_raw_history(tmp_path, failure):
    store = MeetingStore.create(tmp_path, "失败")
    original = seed(store.directory)
    coordinator = ChatCoordinator()
    history = ChatHistory(store.directory)
    history.context.begin("previous")
    history.context.finish(
        "previous", "completed", summary="旧摘要", through_message=2, model="old"
    )
    history.close()
    client = Compressor(fail=failure == "network", text="" if failure == "empty" else "长" * 5000)
    try:
        coordinator.compress(store.directory, store.metadata["id"], client)
        wait(lambda: coordinator.context(store.directory)["status"] == "failed")
        context = coordinator.context(store.directory)
        assert context["summary"] == "旧摘要" and context["through_message"] == 2
        assert "secret" not in context["error"]
        assert coordinator.list(store.directory) == original
    finally:
        coordinator.close()
        store.close()


def test_cancel_blocks_late_result_and_allows_retry_with_meeting_ownership(tmp_path):
    store = MeetingStore.create(tmp_path, "取消")
    seed(store.directory)
    events, client = [], Compressor(block=True)
    coordinator = ChatCoordinator(events.append)
    try:
        sent = coordinator.compress(store.directory, store.metadata["id"], client)
        assert client.entered.wait(2)
        with pytest.raises(RuntimeError):
            coordinator.send(store.directory, store.context_snapshot(), "冲突", Client())
        with pytest.raises(RuntimeError):
            coordinator.configure_context(store.directory, store.metadata["id"], reset=True)
        assert not coordinator.cancel(store.metadata["id"], "wrong-request")
        assert coordinator.cancel(store.metadata["id"], sent["request_id"])
        assert coordinator.context(store.directory)["status"] == "cancelled"
        coordinator.compress(store.directory, store.metadata["id"], Compressor(text="重试摘要"))
        wait(lambda: coordinator.context(store.directory)["status"] == "completed")
        client.release.set()
        wait(lambda: sent["request_id"] not in coordinator._workers)
        assert coordinator.context(store.directory)["summary"] == "重试摘要"
        assert all(event["meeting_id"] == store.metadata["id"] for event in events)
    finally:
        client.release.set()
        coordinator.close()
        store.close()


def test_recovery_and_short_history(tmp_path):
    store = MeetingStore.create(tmp_path, "恢复")
    seed(store.directory, count=2)
    history = ChatHistory(store.directory)
    history.context.begin("crashed")
    history.close()
    coordinator = ChatCoordinator()
    try:
        assert coordinator.context(store.directory)["status"] == "interrupted"
        with pytest.raises(ValueError, match="最近两轮"):
            coordinator.compress(store.directory, store.metadata["id"], Compressor())
    finally:
        coordinator.close()
        store.close()


def test_large_history_batches_cover_every_character_with_bounded_inputs():
    history = [SimpleNamespace(role="user", text="中文问题" * 5000)]
    history += [SimpleNamespace(role="assistant", text="answer\n" * 3000)]
    history += [SimpleNamespace(role="user", text="短") for _ in range(400)]
    client = Compressor()
    result = compress_messages(client, history, "之前的摘要", 8192, threading.Event())
    assert result == client.text and len(client.calls) > 2
    all_text = []
    for call in client.calls:
        content = call[0]["content"]
        assert text_tokens(COMPACTION_PROMPT) + text_tokens(content) + 24 <= 8192 - 4096
        payload = json.loads(content)
        all_text.extend(item["text"] for item in payload["older_chat_messages"])
    assert "".join(all_text) == "".join(m.text for m in history)
    assert json.loads(client.calls[0][0]["content"])["previous_chat_summary"] == "之前的摘要"


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
def test_compression_uses_dedicated_prompt_for_all_protocols(tmp_path, protocol):
    store = MeetingStore.create(tmp_path, "协议")
    seed(store.directory, count=3)
    coordinator = ChatCoordinator()
    try:
        with server_response(completed(protocol, "保留关键结论。")) as (base, received):
            client = create_summary_client(
                SummarySettings(
                    base,
                    "chat-model",
                    "fixture-key",
                    system_prompt=COMPACTION_PROMPT,
                    protocol=protocol,
                )
            )
            coordinator.compress(store.directory, store.metadata["id"], client)
            wait(lambda: coordinator.context(store.directory)["status"] != "compressing")
            assert coordinator.context(store.directory)["status"] == "completed"
            body = received[0][2]
            assert body["model"] == "chat-model"
            prompt = (
                body["instructions"]
                if protocol == "responses"
                else body["system"]
                if protocol == "anthropic"
                else body["messages"][0]["content"]
            )
            assert prompt == COMPACTION_PROMPT
            assert "meeting_reference" not in str(body)
    finally:
        coordinator.close()
        store.close()
