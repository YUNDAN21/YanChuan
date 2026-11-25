import threading
from types import SimpleNamespace

import pytest
from livecaptions_meetings import COMPACTION_PROMPT, ApiProfile, ChatHistory, MeetingStore
from livecaptions_service import DesktopService
from test_service import Runtime, configured, wait

from livecaptions_asr.results import Sentence, StreamEvent


def seed(directory):
    history = ChatHistory(directory)
    try:
        for index in range(5):
            history.begin(str(index), "历史问题" * 100, "old", 0)
            history.finish(str(index), "completed", text="历史回答" * 100)
    finally:
        history.close()


def test_compaction_uses_chat_snapshot_while_recording_and_summary_are_independent(tmp_path):
    configured(tmp_path)
    runtime, captured = Runtime(), []
    entered, release = threading.Event(), threading.Event()

    def factory(settings):
        captured.append(settings)

        def chat(messages):
            entered.set()
            assert release.wait(4)
            assert "MEETING_ONLY" not in str(messages)
            return "对话摘要"

        return SimpleNamespace(model=settings.model, chat=chat)

    service = DesktopService(tmp_path, runtime=runtime, chat_client_factory=factory)
    try:
        service.configure_schedule(5, False)
        service.set_api_profiles(
            [
                ApiProfile("summary", "总结", "http://localhost/v1", "summary-model", "fixture"),
                ApiProfile(
                    "chat", "问答", "http://localhost/v1", "chat-model", "fixture", "responses"
                ),
            ],
            "summary",
        )
        service.configure_chat("chat", model="chat-override", reasoning_effort="high")
        service.start()
        assert runtime.entered.wait(2)
        meeting_id = service.store.metadata["id"]
        seed(service.store.directory)
        service.compress_chat(meeting_id)
        assert entered.wait(2)
        service.configure_chat(model="next-chat-model")
        service._ingest(
            [StreamEvent("sentence", sentence=Sentence(4, 0, 2, "MEETING_ONLY", (4,), "end"))]
        )
        assert service.active and service.store.entries()[-1].text == "MEETING_ONLY"
        assert service.store.metadata["summary_cursor"] == 0
        assert captured[0].model == "chat-override" and captured[0].protocol == "responses"
        assert captured[0].system_prompt == COMPACTION_PROMPT
        assert captured[0].reasoning_effort == "high"
        assert service.settings.model == "summary-model"
        release.set()
        wait(lambda: service.chat_messages()["context"]["status"] == "completed")
        service.stop()
        assert service.wait(3)
    finally:
        release.set()
        service.close()


def test_compaction_finishes_in_original_meeting_and_memory_persists(tmp_path):
    configured(tmp_path)
    entered, release = threading.Event(), threading.Event()
    events = []

    def chat(messages):
        entered.set()
        assert release.wait(4)
        return "会议甲的聊天摘要"

    service = DesktopService(
        tmp_path,
        observer=events.append,
        chat_client_factory=lambda _: SimpleNamespace(model="fake", chat=chat),
    )
    first = MeetingStore.create(tmp_path / "data/meetings", "甲")
    first.append("early", 0, 5, "上半段")
    first.append("late", 70, 75, "下半段")
    first_id, directory = first.metadata["id"], first.directory
    first.close()
    second = MeetingStore.create(tmp_path / "data/meetings", "乙")
    other = second.directory
    second.close()
    try:
        service.open_meeting(directory)
        seed(directory)
        service.compress_chat(first_id)
        assert entered.wait(2)
        service.open_meeting(other)
        release.set()
        wait(lambda: service.chat.context(directory)["status"] == "completed")
        assert service.chat_messages()["context"]["summary"] == ""
        assert any(
            e.name == "chat.updated"
            and e.payload["meeting_id"] == first_id
            and e.payload["context"]["status"] == "completed"
            for e in events
        )
        with pytest.raises(ValueError, match="切换"):
            service.configure_chat_context(first_id, reset=True)
        service.open_meeting(directory)
        assert service.chat_messages()["context"]["summary"] == "会议甲的聊天摘要"
        before = service.chat_usage()["history_tokens"]
        service.configure_chat_context(first_id, reset=True)
        assert service.chat_usage()["history_tokens"] > before
    finally:
        release.set()
        service.close()
