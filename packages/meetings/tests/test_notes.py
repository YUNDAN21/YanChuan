import pytest
from livecaptions_meetings import NoteStore


def test_notes_groups_crud_and_restart(tmp_path):
    path = tmp_path / "notes.sqlite3"
    store = NoteStore(path)
    group = store.create_group("  笔记组  ", "meeting-a")
    assert store.create_group("重复", "meeting-a")["id"] == group["id"]
    other = store.create_group("手动组")
    note = store.create(group["id"], "原名", "# 正文\n\n$x^2$")
    store.update(note["id"], "新名", "编辑后的正文")
    store.rename_group(group["id"], "组新名")
    assert store.list(other["id"]) == []
    store.close()
    store = NoteStore(path)
    assert store.get(note["id"])["content"] == "编辑后的正文"
    assert store.get(note["id"])["title"] == "新名"
    assert next(g for g in store.groups() if g["id"] == group["id"])["title"] == "组新名"
    store.delete(note["id"])
    with pytest.raises(ValueError, match="不存在"):
        store.get(note["id"])
    remaining = store.create(group["id"])
    store.delete_group(group["id"])
    with pytest.raises(ValueError, match="不存在"):
        store.get(remaining["id"])
    assert store.groups()[0]["id"] == other["id"]
    store.close()


def test_source_dedup_preserves_edits_and_validation(tmp_path):
    store = NoteStore(tmp_path / "notes.sqlite3")
    group = store.create_group("会议")
    note = store.create(group["id"], "来源", "回答", "request")
    store.update(note["id"], "自己的标题", "自己的编辑")
    assert store.create(group["id"], "重复", "覆盖", "request")["content"] == "自己的编辑"
    assert len(store.list(group["id"])) == 1
    for bad in ("", " ", "a\nb"):
        with pytest.raises(ValueError, match="名称"):
            store.update(note["id"], bad, "不会保存")
    assert store.get(note["id"])["content"] == "自己的编辑"
    with pytest.raises(ValueError, match="不存在"):
        store.create("missing")
    store.close()
