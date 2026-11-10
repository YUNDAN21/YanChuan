"""Local notebooks, independent of transcription and model requests."""

import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from .store import utc_now


def title_text(value):
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ValueError("名称须为非空单行文字")
    return value.strip()


class NoteStore:
    """Notebook storage. Meeting links identify groups; notes survive meeting deletion."""

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS note_group(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, meeting_id TEXT UNIQUE,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS note(
                    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES note_group(id)
                    ON DELETE CASCADE, title TEXT NOT NULL, content TEXT NOT NULL,
                    source_request_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(group_id, source_request_id));
            """)

    def groups(self):
        with self._lock:
            return [
                dict(row)
                for row in self._db.execute(
                    """SELECT g.*, COUNT(n.id) AS note_count FROM note_group g
                LEFT JOIN note n ON n.group_id=g.id GROUP BY g.id
                ORDER BY g.updated_at DESC, g.id"""
                )
            ]

    def _group(self, group_id):
        row = self._db.execute("SELECT * FROM note_group WHERE id=?", (group_id,)).fetchone()
        if row is None:
            raise ValueError("笔记分组不存在")
        return dict(row)

    def create_group(self, title, meeting_id=None):
        title = title_text(title)
        with self._lock, self._db:
            if meeting_id:
                row = self._db.execute(
                    "SELECT * FROM note_group WHERE meeting_id=?", (meeting_id,)
                ).fetchone()
                if row:
                    return dict(row)
            identifier, now = uuid4().hex, utc_now()
            self._db.execute(
                "INSERT INTO note_group VALUES(?,?,?,?,?)",
                (identifier, title, meeting_id, now, now),
            )
            return self._group(identifier)

    def rename_group(self, group_id, title):
        with self._lock, self._db:
            self._group(group_id)
            self._db.execute(
                "UPDATE note_group SET title=?,updated_at=? WHERE id=?",
                (title_text(title), utc_now(), group_id),
            )
            return self._group(group_id)

    def delete_group(self, group_id):
        with self._lock, self._db:
            self._group(group_id)
            self._db.execute("DELETE FROM note_group WHERE id=?", (group_id,))

    def list(self, group_id):
        with self._lock:
            self._group(group_id)
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM note WHERE group_id=? ORDER BY updated_at DESC,id", (group_id,)
                )
            ]

    def get(self, note_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM note WHERE id=?", (note_id,)).fetchone()
            if row is None:
                raise ValueError("笔记不存在")
            return dict(row)

    def create(self, group_id, title="新笔记", content="", source_request_id=None):
        title = title_text(title)
        if not isinstance(content, str):
            raise TypeError("笔记正文须为文字")
        with self._lock, self._db:
            self._group(group_id)
            if source_request_id:
                row = self._db.execute(
                    "SELECT * FROM note WHERE group_id=? AND source_request_id=?",
                    (group_id, source_request_id),
                ).fetchone()
                if row:
                    return dict(row)
            identifier, now = uuid4().hex, utc_now()
            self._db.execute(
                "INSERT INTO note VALUES(?,?,?,?,?,?,?)",
                (identifier, group_id, title, content, source_request_id, now, now),
            )
            self._db.execute("UPDATE note_group SET updated_at=? WHERE id=?", (now, group_id))
            return self.get(identifier)

    def update(self, note_id, title, content):
        title = title_text(title)
        if not isinstance(content, str):
            raise TypeError("笔记正文须为文字")
        with self._lock, self._db:
            note = self.get(note_id)
            now = utc_now()
            self._db.execute(
                "UPDATE note SET title=?,content=?,updated_at=? WHERE id=?",
                (title, content, now, note_id),
            )
            self._db.execute(
                "UPDATE note_group SET updated_at=? WHERE id=?", (now, note["group_id"])
            )
            return self.get(note_id)

    def delete(self, note_id):
        with self._lock, self._db:
            self.get(note_id)
            self._db.execute("DELETE FROM note WHERE id=?", (note_id,))

    def close(self):
        with self._lock:
            self._db.close()
