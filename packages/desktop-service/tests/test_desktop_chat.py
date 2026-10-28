import threading

import pytest
from livecaptions_meetings import ApiProfile, MeetingStore
from livecaptions_service import DesktopService
from test_service import Client, Runtime, configured, wait


def test_usage_current_meeting_draft_and_capacity_without_ai(tmp_path):
    configured(tmp_path)
    service = DesktopService(tmp_path)
    try:
        assert service.chat_usage()["meeting_id"] is None
        store = MeetingStore.create(tmp_path / "data/meetings", "用量测试")
        store.append("1", 0, 5, "中文会议原文" * 40)
        directory = store.directory
        store.close()
        service.open_meeting(directory)
        before = service.chat_usage()
        assert before["meeting_id"] == service.store.metadata["id"]
        assert (
            service.chat_usage("英文 Japanese 中文" * 20)["estimated_tokens"]
            > before["estimated_tokens"]
        )
        service.configure_chat("default", 8192)
        smaller = service.chat_usage()
        assert smaller["context_tokens"] == 8192
        assert smaller["total_tokens"] == before["total_tokens"]
        assert service.chat_messages()["messages"] == []
        assert service.store.metadata["summary_cursor"] == 0
    finally:
        service.close()


class ChatClient:
    model = "independent-chat"

    def __init__(self):
        self.received = []
        self.release = threading.Event()

    def chat(self, messages):
        self.received.append(messages)
        assert self.release.wait(5)
        return "周五。[00:00:00]"


def test_chat_rename_while_recording_and_meeting_switch(tmp_path):
    configured(tmp_path)
    runtime, events, clients, settings = Runtime(), [], [], []

    def factory(value):
        settings.append(value)
        client = ChatClient()
        clients.append(client)
        return client

    service = DesktopService(
        tmp_path,
        runtime=runtime,
        observer=events.append,
        client_factory=lambda _: Client(),
        chat_client_factory=factory,
    )
    service.configure_schedule(5, False)
    profiles = [
        *service.settings.api_profiles,
        ApiProfile("chat", "聊天", "http://127.0.0.1:2/v1", "chat-model", "chat-secret"),
    ]
    service.set_api_profiles(profiles, "default")
    service.configure_chat("chat", 999000)
    service.configure_chat(model="chosen-chat-model", reasoning_effort="high")
    service.start(title="原名")
    assert runtime.entered.wait(2)
    service.rename_meeting("新名")
    original = service.store.directory
    assert service.list_meetings()[0]["title"] == "新名" and service.active
    service.send_chat("何时完成？")
    service.configure_chat("default", 272000)
    assert settings[0].model == "chosen-chat-model" and "会议问答助手" in settings[0].system_prompt
    assert settings[0].reasoning_effort == "high"
    assert service.settings.model == "test"
    assert service.settings.chat_model == ""
    service.stop()
    assert service.wait(5)
    another = MeetingStore.create(tmp_path / "data/meetings", "其他会议")
    another_directory = another.directory
    another.close()
    service.open_meeting(another_directory)
    assert not service.chat_messages()["messages"]
    clients[0].release.set()
    wait(
        lambda: any(
            e.name == "chat.updated" and e.payload["messages"][-1]["status"] == "completed"
            for e in events
        )
    )
    assert service.store.directory == another_directory and not service.chat_messages()["messages"]
    service.open_meeting(original)
    assert service.chat_messages()["messages"][-1]["text"].startswith("周五")
    assert service.store.metadata["summary_cursor"] == 0
    assert "新名" in service.meeting_content()["text"]
    service.close()


def test_old_settings_defaults_independence_and_validation(tmp_path):
    configured(tmp_path)
    service = DesktopService(tmp_path)
    assert service.settings.chat_context_tokens == 272000
    assert service.chat_messages()["messages"] == []
    with pytest.raises(ValueError):
        service.configure_chat("missing", 272000)
    with pytest.raises(ValueError):
        service.configure_chat("default", 1)
    with pytest.raises(ValueError):
        service.send_chat("hello")
    service.configure_chat("default", 1000000)
    service.close()
    resumed = DesktopService(tmp_path)
    assert resumed.settings.chat_context_tokens == 1000000
    resumed.close()


def test_chat_model_effort_persistence_partial_updates_and_atomic_validation(tmp_path):
    configured(tmp_path)
    service = DesktopService(tmp_path)
    try:
        service.configure_chat(context_tokens=1000000, model="other-model", reasoning_effort="max")
        service.configure_chat(context_tokens=8192)
        assert service.settings.chat_model == "other-model"
        assert service.settings.chat_reasoning_effort == "max"
        assert service.settings.model == "test"
        before = service.configuration()
        with pytest.raises(ValueError, match="思考"):
            service.configure_chat(model="discarded", reasoning_effort="unknown")
        assert service.configuration() == before
        with pytest.raises(ValueError, match="模型"):
            service.configure_chat(model=42)
    finally:
        service.close()
    resumed = DesktopService(tmp_path)
    try:
        assert resumed.settings.chat_model == "other-model"
        assert resumed.settings.chat_reasoning_effort == "max"
        assert resumed.settings.chat_context_tokens == 8192
    finally:
        resumed.close()


def test_chat_can_override_an_empty_profile_model_without_changing_summary(tmp_path):
    service = DesktopService(tmp_path)
    settings = []
    client = ChatClient()
    client.release.set()
    service.chat_client_factory = lambda value: settings.append(value) or client
    profile = ApiProfile("empty", "connection", "http://localhost/v1", "", "local-key")
    # Persistence does not require a ready summary client when no summary is running.
    service.configure_chat(model="chat-only")
    service.settings.api_profiles[0] = profile
    service.settings.selected_api_profile = "empty"
    service.settings.chat_api_profile = "empty"
    store = MeetingStore.create(tmp_path / "data/meetings", "问答")
    directory = store.directory
    store.close()
    try:
        service.open_meeting(directory)
        service.send_chat("问题")
        assert settings[0].model == "chat-only"
        assert service.settings.model == ""
    finally:
        service.close()
