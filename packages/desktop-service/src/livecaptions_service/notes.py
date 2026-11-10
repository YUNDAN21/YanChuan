"""Notebook use cases; storage and chat source resolution without UI dependencies."""

from contextlib import closing
from pathlib import Path

from livecaptions_meetings import ChatHistory, NoteStore, list_meetings


class NotesService:
    def __init__(self, root):
        self.root = Path(root)

    def _store(self):
        return closing(NoteStore(self.root / "data/notes.sqlite3"))

    def groups(self):
        with self._store() as notes:
            return notes.groups()

    def create_group(self, title):
        with self._store() as notes:
            return notes.create_group(title)

    def rename_group(self, group_id, title):
        with self._store() as notes:
            return notes.rename_group(group_id, title)

    def delete_group(self, group_id):
        with self._store() as notes:
            notes.delete_group(group_id)
        return True

    def list(self, group_id):
        with self._store() as notes:
            return notes.list(group_id)

    def create(self, group_id, title="新笔记", content=""):
        with self._store() as notes:
            return notes.create(group_id, title, content)

    def update(self, note_id, title, content):
        with self._store() as notes:
            return notes.update(note_id, title, content)

    def delete(self, note_id):
        with self._store() as notes:
            notes.delete(note_id)
        return True

    def _meeting(self, meeting_id):
        for meeting in list_meetings(self.root / "data/meetings"):
            if meeting["id"] == meeting_id:
                return meeting
        raise ValueError("来源会议不存在")

    def meeting_group(self, meeting_id):
        meeting = self._meeting(meeting_id)
        with self._store() as notes:
            return notes.create_group(meeting["title"], meeting_id)

    def from_chat(self, meeting_id, request_id):
        meeting = self._meeting(meeting_id)
        with closing(ChatHistory(meeting["directory"])) as history:
            messages = [m for m in history.messages() if m.request_id == request_id]
        answer = next(
            (
                m
                for m in messages
                if m.role == "assistant" and m.status == "completed" and m.text.strip()
            ),
            None,
        )
        if answer is None:
            raise ValueError("该回答尚未完成或不存在")
        question = next((m.text for m in messages if m.role == "user"), "会议问答")
        title = " ".join(question.split())[:80] or "会议问答"
        with self._store() as notes:
            group = notes.create_group(meeting["title"], meeting_id)
            return notes.create(group["id"], title, answer.text, request_id)
