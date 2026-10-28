import subprocess
import threading

import pytest
from livecaptions_meetings import (
    ChatCoordinator,
    ChatHistory,
    MeetingStore,
    delete_meeting,
    list_meetings,
)


def test_delete_only_selected_meeting_database_conversation_and_exports(tmp_path):
    root = tmp_path / "meetings"
    store = MeetingStore.create(root, "删除这条")
    entry = store.append("a", 0, 2, "会议原文")
    store.save_summary([entry], "会议总结", "test")
    history = ChatHistory(store.directory)
    history.begin("request", "问题", "test", 1)
    history.finish("request", "completed", text="答案")
    history.close()
    directory, identifier = store.directory, store.metadata["id"]
    store.close()
    other = MeetingStore.create(root, "保留这条")
    other_directory = other.directory
    other.close()
    (tmp_path / "recording.wav").write_bytes(b"keep recording")
    (tmp_path / "settings.json").write_text("keep settings")
    deleted = delete_meeting(root, directory)
    assert deleted["id"] == identifier
    assert not directory.exists()
    assert other_directory.exists()
    assert list_meetings(root)[0]["title"] == "保留这条"
    assert (tmp_path / "recording.wav").read_bytes() == b"keep recording"
    assert (tmp_path / "settings.json").read_text() == "keep settings"
    with pytest.raises(ValueError, match="不存在"):
        delete_meeting(root, directory)


def test_delete_rejects_root_outside_nested_and_non_meeting_targets(tmp_path):
    root = tmp_path / "meetings"
    outside = MeetingStore.create(tmp_path / "outside", "外部")
    outside_dir = outside.directory
    outside.close()
    store = MeetingStore.create(root, "内部")
    directory = store.directory
    store.close()
    ordinary = root / "ordinary"
    ordinary.mkdir()
    (ordinary / "keep.txt").write_text("keep")
    for candidate in [root, tmp_path, outside_dir, ordinary, directory / "nested"]:
        with pytest.raises(ValueError):
            delete_meeting(root, candidate)
    assert (directory / "meeting.sqlite3").exists()
    assert (outside_dir / "meeting.sqlite3").exists()
    assert (ordinary / "keep.txt").exists()


def test_delete_rejects_windows_junction_without_touching_target(tmp_path):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows junction validation")
    root = tmp_path / "meetings"
    store = MeetingStore.create(root, "保留")
    directory = store.directory
    store.close()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    junction = directory / "linked"
    # A junction can be created without Windows developer mode or symlink privileges.
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match="链接"):
            delete_meeting(root, directory)
        with pytest.raises(ValueError):
            delete_meeting(root, junction)
        assert (outside / "keep.txt").read_text() == "keep"
        assert (directory / "meeting.sqlite3").exists()
    finally:
        junction.rmdir()


@pytest.mark.parametrize("cancel_first", [False, True])
def test_delete_during_chat_releases_database_and_drops_late_reply(tmp_path, cancel_first):
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    class Client:
        model = "test"

        def chat(self, messages):
            entered.set()
            assert release.wait(5)
            done.set()
            return "迟到结果"

    store = MeetingStore.create(tmp_path, "问答")
    directory = store.directory
    snapshot = store.context_snapshot()
    store.close()
    updates = []
    coordinator = ChatCoordinator(updates.append)
    coordinator.send(directory, snapshot, "问题", Client())
    assert entered.wait(3)
    if cancel_first:
        coordinator.cancel(snapshot["meeting_id"])
    coordinator.forget(snapshot["meeting_id"], directory)
    before = len(updates)
    delete_meeting(tmp_path, directory)
    release.set()
    assert done.wait(3)
    coordinator.close()
    assert not directory.exists()
    assert len(updates) == before
