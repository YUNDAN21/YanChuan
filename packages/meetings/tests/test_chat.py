import json
import threading
import time

import pytest
from livecaptions_meetings import (
    ChatCoordinator,
    ChatHistory,
    MeetingStore,
    SummarySettings,
    chat_context_usage,
    create_summary_client,
)
from test_protocols import completed, server_response


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


class Client:
    model = "question-model"

    def __init__(self, *, block=False, fail=False):
        self.entered, self.release = threading.Event(), threading.Event()
        if not block:
            self.release.set()
        self.fail, self.received = fail, []

    def chat(self, messages):
        self.received.append(messages)
        self.entered.set()
        assert self.release.wait(5)
        if self.fail:
            raise RuntimeError("private-secret response body")
        return "周五交付。[00:00:03]"


def test_context_preview_matches_send_and_tracks_pending_and_completed_history(tmp_path):
    store = MeetingStore.create(tmp_path, "上下文")
    store.append("a", 0, 8, "讨论下周发布计划。")
    coordinator, client = ChatCoordinator(), Client(block=True)
    try:
        snapshot = store.context_snapshot()
        usage = chat_context_usage(snapshot["content"], [], "何时发布？")
        sent = coordinator.send(store.directory, snapshot, "何时发布？", client)
        assert usage["estimated_tokens"] == sent["estimated_tokens"]
        assert usage["total_tokens"] == sent["estimated_tokens"] + 4096
        assert not usage["over_budget"]
        pending_usage = chat_context_usage(snapshot["content"], coordinator.list(store.directory))
        assert pending_usage == usage
        client.release.set()
        wait(lambda: coordinator.list(store.directory)[-1].status == "completed")
        history = coordinator.list(store.directory)
        after = chat_context_usage(snapshot["content"], history)
        assert after["estimated_tokens"] > usage["estimated_tokens"]
        assert chat_context_usage(snapshot["content"], history, context_tokens=1)["over_budget"]
        assert (
            chat_context_usage(snapshot["content"], history, "长问题" * 100)["estimated_tokens"]
            > after["estimated_tokens"]
        )
    finally:
        client.release.set()
        coordinator.close()
        store.close()


def test_rename_and_canonical_complete_content(tmp_path):
    store = MeetingStore.create(tmp_path, "旧名称")
    entries = [store.append(str(i), i, i + 1, f"第{i}条") for i in range(350)]
    store.save_summary(entries, "**全部结论**", "model")
    directory = store.directory
    store.rename("重命名会议")
    assert store.directory == directory
    assert json.loads((directory / "meeting.json").read_text("utf-8"))["title"] == "重命名会议"
    for kind, name in [("transcript", "transcript.md"), ("summaries", "summaries.md")]:
        assert store.content(kind) == (directory / name).read_text("utf-8")
    assert "第349条" in store.content() and "全部结论" in store.content()
    assert "[00:05:49–00:05:50]" in store.content()
    with pytest.raises(ValueError):
        store.rename(" \n ")
    store.close()
    reopened = MeetingStore(directory)
    assert reopened.metadata["title"] == "重命名会议"
    reopened.close()


def test_full_context_snapshot_multiturn_and_reopen(tmp_path):
    store = MeetingStore.create(tmp_path, "问答")
    store.append("a", 3, 8, "原文" * 8000 + "周五交付")
    coordinator, client = ChatCoordinator(), Client(block=True)
    first = coordinator.send(store.directory, store.context_snapshot(), "何时交付？", client)
    assert client.entered.wait(2)
    store.append("b", 9, 10, "后来补充")
    assert "后来补充" not in client.received[0][0]["content"]
    assert "原文" * 8000 in client.received[0][0]["content"]
    with pytest.raises(RuntimeError):
        coordinator.send(store.directory, store.context_snapshot(), "重复", client)
    client.release.set()
    wait(lambda: coordinator.list(store.directory)[-1].status == "completed")
    second = Client()
    coordinator.send(store.directory, store.context_snapshot(), "再说一遍", second)
    wait(
        lambda: (
            len(coordinator.list(store.directory)) == 4
            and coordinator.list(store.directory)[-1].status == "completed"
        )
    )
    assert [m["role"] for m in second.received[0]] == ["user", "assistant", "user"]
    assert "后来补充" in second.received[0][0]["content"]
    assert store.metadata["summary_cursor"] == 0
    coordinator.close()
    history = ChatHistory(store.directory)
    assert history.messages()[1].request_id == first["request_id"]
    assert len(history.messages()) == 4
    history.close()
    store.close()


def test_cancel_retry_and_late_answer_cannot_overwrite(tmp_path):
    store = MeetingStore.create(tmp_path, "中断")
    coordinator, client = ChatCoordinator(), Client(block=True)
    request = coordinator.send(store.directory, store.context_snapshot(), "问题", client)
    assert client.entered.wait(2)
    assert coordinator.cancel(store.metadata["id"], request["request_id"])
    newer = Client()
    coordinator.send(store.directory, store.context_snapshot(), "重试", newer)
    wait(lambda: coordinator.list(store.directory)[-1].status == "completed")
    client.release.set()
    wait(lambda: coordinator.list(store.directory)[1].status == "cancelled")
    assert len(newer.received[0]) == 1  # cancelled turns are not conversation context
    assert coordinator.list(store.directory)[1].text == ""
    coordinator.close()
    store.close()


def test_failures_recovery_and_budget_do_not_advance_summary(tmp_path):
    store = MeetingStore.create(tmp_path, "失败")
    coordinator = ChatCoordinator()
    coordinator.send(store.directory, store.context_snapshot(), "问题", Client(fail=True))
    wait(lambda: coordinator.list(store.directory)[-1].status == "failed")
    assert "private-secret" not in coordinator.list(store.directory)[-1].error
    history = ChatHistory(store.directory)
    history.begin("crashed", "意外退出", "model", 0)
    history.close()
    resumed = ChatCoordinator()
    assert resumed.list(store.directory)[-1].status == "interrupted"
    with pytest.raises(ValueError, match="预算"):
        resumed.send(
            store.directory, store.context_snapshot(), "内容" * 10000, Client(), context_tokens=8192
        )
    assert store.metadata["summary_cursor"] == 0
    coordinator.close()
    resumed.close()
    store.close()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
def test_multiturn_real_http_all_protocols(protocol):
    messages = [
        {"role": "user", "content": "会议原文"},
        {"role": "assistant", "content": "首次回答"},
        {"role": "user", "content": "追问"},
    ]
    with server_response(completed(protocol, "再次回答")) as (base, received):
        client = create_summary_client(
            SummarySettings(base, "chosen-model", "test-key", protocol=protocol)
        )
        assert client.chat(messages) == "再次回答"
        body = received[0][2]
        turns = body["input"] if protocol == "responses" else body["messages"]
        assert turns[-3:] == messages
        if protocol == "responses":
            assert body["store"] is False


def test_observer_failure_and_close_preserve_interrupted_message(tmp_path):
    store = MeetingStore.create(tmp_path, "收尾")
    client = Client(block=True)

    def broken_observer(value):
        raise RuntimeError("observer failed")

    coordinator = ChatCoordinator(broken_observer)
    coordinator.send(store.directory, store.context_snapshot(), "问题", client)
    assert client.entered.wait(2)
    coordinator.close()
    history = ChatHistory(store.directory)
    assert history.messages()[-1].status == "interrupted"
    client.release.set()
    history.close()
    store.close()
