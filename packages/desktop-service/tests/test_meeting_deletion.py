import threading

import pytest
from livecaptions_meetings import MeetingStore
from livecaptions_service import DesktopService
from test_service import Client, Runtime, configured


def meeting(root, name):
    store = MeetingStore.create(root / "data/meetings", name)
    store.append("a", 0, 2, "原文")
    directory = store.directory
    store.close()
    return directory


def test_delete_history_preserves_current_and_delete_current_resets_snapshot(tmp_path):
    first, second = meeting(tmp_path, "第一条"), meeting(tmp_path, "第二条")
    events = []
    service = DesktopService(tmp_path, observer=events.append)
    try:
        service.open_meeting(second)
        current_id = service.store.metadata["id"]
        deleted = service.delete_meeting(first)
        assert not deleted["current"] and not first.exists()
        assert service.store.metadata["id"] == current_id
        deleted = service.delete_meeting(second)
        assert deleted["current"] and not second.exists()
        assert service.snapshot()["meeting"] is None
        assert service.snapshot()["state"] == "idle"
        with pytest.raises(ValueError, match="打开"):
            service.meeting_content()
        assert service.chat_messages() == {"meeting_id": None, "messages": []}
        assert service.list_meetings() == []
        assert [event.name for event in events].count("meeting.deleted") == 2
    finally:
        service.close()


def test_current_recording_cannot_be_deleted_and_survives_stop(tmp_path):
    configured(tmp_path)
    runtime = Runtime()
    service = DesktopService(tmp_path, runtime=runtime, client_factory=lambda _: Client())
    service.configure_schedule(5, False)
    try:
        service.start(title="录音中")
        assert runtime.entered.wait(3)
        directory = service.store.directory
        with pytest.raises(RuntimeError, match="结束"):
            service.delete_meeting(directory)
        assert directory.exists() and service.active
        service.stop()
        assert service.wait(5)
        assert service.store.entries()[-1].text == "测试完成。"
        assert service.delete_meeting(directory)["current"]
    finally:
        service.close()


def test_delete_waits_for_manual_summary_to_finish_before_permitting(tmp_path):
    configured(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class SlowClient(Client):
        def summarize(self, *args):
            entered.set()
            assert release.wait(5)
            return "总结"

    service = DesktopService(tmp_path, client_factory=lambda _: SlowClient())
    directory = meeting(tmp_path, "总结中")
    try:
        service.open_meeting(directory)
        service.summarize_now()
        assert entered.wait(3)
        with pytest.raises(RuntimeError, match="总结"):
            service.delete_meeting(directory)
        assert directory.exists()
    finally:
        release.set()
        service.close()


def test_delete_failure_reopens_current_record_without_emitting_deleted(tmp_path, monkeypatch):
    from livecaptions_service import service as module

    directory = meeting(tmp_path, "保留")
    events = []
    service = DesktopService(tmp_path, observer=events.append)

    def denied(*args):
        raise PermissionError("file occupied")

    monkeypatch.setattr(module, "delete_meeting", denied)
    try:
        service.open_meeting(directory)
        identifier = service.store.metadata["id"]
        with pytest.raises(RuntimeError, match="占用"):
            service.delete_meeting(directory)
        assert service.store.metadata["id"] == identifier
        assert service.store.entries()[0].text == "原文"
        assert (directory / "meeting.sqlite3").exists()
        assert not any(event.name == "meeting.deleted" for event in events)
    finally:
        service.close()
