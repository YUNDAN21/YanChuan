import threading
from types import SimpleNamespace

import pytest
from livecaptions_backend.protocol import Router
from livecaptions_meetings import MeetingStore
from livecaptions_service import DesktopService


def test_context_reset_requires_matching_meeting_and_retrieval_usage(tmp_path):
    service = DesktopService(
        tmp_path,
        chat_client_factory=lambda _: SimpleNamespace(model="fake", chat=lambda _: "摘要"),
    )
    service.store = MeetingStore.create(tmp_path / "data/meetings", "上下文")
    meeting_id = service.store.metadata["id"]
    router = Router(service)
    try:
        original = service.configuration()
        with pytest.raises(ValueError, match="切换"):
            router.dispatch("chat.context.reset", {"meeting_id": "other"})
        context = router.dispatch("chat.context.reset", {"meeting_id": meeting_id})
        assert "range_start" not in context and "range_end" not in context
        assert not router.dispatch("chat.usage", {})["compressed"]
        assert not router.dispatch("chat.usage", {})["can_compress"]
        usage = router.dispatch("chat.usage", {})
        assert not usage["retrieval_mode"] and usage["tool_tokens"] == 0
        assert service.configuration() == original
    finally:
        service.close()


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "failed"])
def test_chat_preview_is_bound_to_the_pending_request(tmp_path, outcome):
    ready, release, done = threading.Event(), threading.Event(), threading.Event()
    events = []

    def observe(event):
        events.append(event)
        if event.name == "chat.preview":
            ready.set()
        if event.name == "chat.updated" and any(
            reply["role"] == "assistant" and reply["status"] != "pending"
            for reply in event.payload["messages"]
        ):
            done.set()

    def stream(messages, cancelled, *, on_delta, request_id):
        on_delta('{"text":"周五')
        assert release.wait(5)
        on_delta("验收。")
        return '{"text":"周五验收。","citations":[' + ("999" if outcome == "failed" else "0") + "]}"

    service = DesktopService(
        tmp_path,
        observer=observe,
        chat_client_factory=lambda _: SimpleNamespace(model="fixture", stream_chat=stream),
    )
    service.set_api_profiles([{"id": "agy", "name": "agy", "connection": "agy"}], "agy")
    service.store = MeetingStore.create(tmp_path / "data/meetings", "合成会议")
    service.store.append("sample", 3, 8, "决定周五验收。")
    router = Router(service)
    try:
        request = router.dispatch("chat.send", {"question": "何时验收？"})
        assert ready.wait(5)
        snapshot = router.dispatch("chat.list", {})
        assert snapshot["preview"] == {
            "meeting_id": request["meeting_id"],
            "request_id": request["request_id"],
            "text": "周五",
            "revision": 1,
        }
        assert snapshot["messages"][-1]["text"] == "" and not done.is_set()
        if outcome == "cancelled":
            assert router.dispatch("chat.cancel", {"request_id": request["request_id"]})
        release.set()
        assert done.wait(5)
        result = router.dispatch("chat.list", {})
        assert result["preview"] is None
        answer = result["messages"][-1]
        assert answer["status"] == outcome
        assert answer["text"] == (
            "周五验收。\n\n来源：[00:00:03–00:00:08]" if outcome == "completed" else ""
        )
        finished_at = next(
            event.sequence
            for event in events
            if event.name == "chat.updated"
            and any(
                message["role"] == "assistant" and message["status"] == outcome
                for message in event.payload["messages"]
            )
        )
        assert all(event.sequence < finished_at for event in events if event.name == "chat.preview")
    finally:
        release.set()
        service.close()
