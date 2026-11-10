import pytest
from livecaptions_meetings import ChatHistory, MeetingStore
from livecaptions_service import DesktopService


def seed(root, title):
    store = MeetingStore.create(root / "data/meetings", title)
    store.append("a", 0, 2, "测试")
    history = ChatHistory(store.directory)
    history.begin("req", "什么是公式？", "model", 1)
    history.finish("req", "completed", text="公式 $x^2$\n\n$$\ny=1\n$$")
    history.close()
    meta, directory = store.metadata, store.directory
    store.close()
    return meta["id"], directory


def test_note_source_bound_to_explicit_meeting_and_survives_deletion(tmp_path):
    first, first_path = seed(tmp_path, "第一场")
    second, second_path = seed(tmp_path, "第二场")
    events = []
    service = DesktopService(tmp_path, observer=events.append)
    try:
        service.open_meeting(second_path)
        note = service.note_call("from_chat", meeting_id=first, request_id="req")
        groups = service.note_call("groups")
        assert groups[0]["meeting_id"] == first
        assert groups[0]["title"] == "第一场"
        service.note_call("group.rename", group_id=note["group_id"], title="我的整理")
        assert service.note_call("meeting_group", meeting_id=first)["title"] == "我的整理"
        assert service.store.metadata["id"] == second
        assert service.store.metadata["summary_cursor"] == 0
        assert service.note_call("from_chat", meeting_id=first, request_id="req") == note
        service.note_call("update", note_id=note["id"], title="保存的公式", content="自己整理")
        assert (
            service.note_call("from_chat", meeting_id=first, request_id="req")["content"]
            == "自己整理"
        )
        with pytest.raises(ValueError, match="不存在"):
            service.note_call("from_chat", meeting_id=first, request_id="unknown")
        service.delete_meeting(first_path)
        assert service.note_call("list", group_id=note["group_id"])[0]["content"] == "自己整理"
        assert any(e.name == "notes.changed" for e in events)
        assert not service.active
    finally:
        service.close()


def test_unfinished_answer_does_not_create_note_or_group(tmp_path):
    identifier, path = seed(tmp_path, "尚未完成")
    history = ChatHistory(path)
    history.begin("pending", "尚在回答", "model", 1)
    history.close()
    service = DesktopService(tmp_path)
    try:
        with pytest.raises(ValueError, match="尚未完成"):
            service.note_call("from_chat", meeting_id=identifier, request_id="pending")
        assert service.note_call("groups") == []
    finally:
        service.close()


def test_standalone_notes_with_no_meeting(tmp_path):
    service = DesktopService(tmp_path)
    try:
        group = service.note_call("group.create", title="独立笔记")
        note = service.note_call("create", group_id=group["id"], title="手动", content="正文")
        service.note_call("group.rename", group_id=group["id"], title="已改名")
        assert service.note_call("groups")[0]["title"] == "已改名"
        service.note_call("delete", note_id=note["id"])
        service.note_call("group.delete", group_id=group["id"])
        assert service.note_call("groups") == []
    finally:
        service.close()
