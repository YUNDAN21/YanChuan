"""Durable meeting notes. SQLite is canonical; Markdown is a readable export."""

import json
import math
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def utc_now():
    return datetime.now(UTC).isoformat()


def timestamp(seconds: float) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class TranscriptEntry:
    id: int
    source_key: str
    start_seconds: float
    end_seconds: float
    text: str

    def markdown(self):
        return f"[{timestamp(self.start_seconds)}–{timestamp(self.end_seconds)}] {self.text}"


@dataclass(frozen=True)
class MeetingSummary:
    id: int
    first_entry: int
    last_entry: int
    start_seconds: float
    end_seconds: float
    text: str
    model: str
    created_at: str


class MeetingStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.directory / "meeting.sqlite3", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")

    @classmethod
    def create(cls, root: Path, title: str):
        directory = Path(root) / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        store = cls(directory)
        store._db.executescript("""
            CREATE TABLE meeting(id TEXT PRIMARY KEY, title TEXT, started_at TEXT,
                                 ended_at TEXT, status TEXT, summary_cursor INTEGER DEFAULT 0);
            CREATE TABLE transcript(id INTEGER PRIMARY KEY, source_key TEXT UNIQUE,
                                    start_seconds REAL, end_seconds REAL, text TEXT);
            CREATE TABLE summary(id INTEGER PRIMARY KEY, first_entry INTEGER,
                last_entry INTEGER UNIQUE, start_seconds REAL, end_seconds REAL,
                text TEXT, model TEXT, created_at TEXT);
            CREATE TABLE summary_error(id INTEGER PRIMARY KEY, message TEXT, created_at TEXT);
        """)
        with store._db:
            store._db.execute(
                "INSERT INTO meeting(id,title,started_at,status) VALUES(?,?,?,?)",
                (uuid4().hex, title.strip() or "未命名会议", utc_now(), "preparing"),
            )
        store.export()
        return store

    @property
    def metadata(self):
        with self._lock:
            return dict(self._db.execute("SELECT * FROM meeting").fetchone())

    @property
    def last_entry(self):
        with self._lock:
            return self._db.execute("SELECT COALESCE(MAX(id),0) FROM transcript").fetchone()[0]

    def append(self, source_key: str, start_seconds: float, end_seconds: float, text: str):
        if (
            not all(math.isfinite(t) and t >= 0 for t in (start_seconds, end_seconds))
            or end_seconds < start_seconds
        ):
            raise ValueError("转写时间范围无效")
        text = text.strip()
        if not text:
            return None
        with self._lock:
            with self._db:
                if self._db.execute(
                    "SELECT id FROM transcript WHERE source_key=?", (source_key,)
                ).fetchone():
                    return None
                result = self._db.execute(
                    "INSERT INTO transcript(source_key,start_seconds,end_seconds,text) VALUES(?,?,?,?)",
                    (source_key, start_seconds, end_seconds, text),
                )
                entry = TranscriptEntry(
                    result.lastrowid, source_key, start_seconds, end_seconds, text
                )
            with (self.directory / "transcript.md").open("a", encoding="utf-8") as output:
                output.write(entry.markdown() + "\n\n")
        return entry

    def entries(self, after=0, through=None):
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM transcript WHERE id>? AND id<=? ORDER BY id",
                (after, self.last_entry if through is None else through),
            ).fetchall()
            return [TranscriptEntry(**dict(row)) for row in rows]

    def pending_batch(self, through: int, max_chars=12000):
        selected, count = [], 0
        for entry in self.entries(self.metadata["summary_cursor"], through):
            if selected and count + len(entry.markdown()) > max_chars:
                break
            if len(entry.markdown()) > max_chars:
                raise ValueError("单条转写超过总结输入预算")
            selected.append(entry)
            count += len(entry.markdown())
        return selected

    def summaries(self):
        with self._lock:
            return [
                MeetingSummary(**dict(row))
                for row in self._db.execute("SELECT * FROM summary ORDER BY id").fetchall()
            ]

    def save_summary(self, entries: list[TranscriptEntry], text: str, model: str):
        if not text.strip() or not entries:
            raise ValueError("总结内容或范围为空")
        with self._lock, self._db:
            cursor = self.metadata["summary_cursor"]
            expected = [entry.id for entry in self.entries(cursor, entries[-1].id)]
            if expected != [entry.id for entry in entries]:
                raise ValueError("总结范围与当前进度不一致")
            result = self._db.execute(
                "INSERT INTO summary(first_entry,last_entry,start_seconds,end_seconds,text,model,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    entries[0].id,
                    entries[-1].id,
                    entries[0].start_seconds,
                    entries[-1].end_seconds,
                    text.strip(),
                    model,
                    utc_now(),
                ),
            )
            self._db.execute("UPDATE meeting SET summary_cursor=?", (entries[-1].id,))
            summary_id = result.lastrowid
        self.export()
        return next(summary for summary in self.summaries() if summary.id == summary_id)

    def record_summary_error(self, message: str):
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO summary_error(message,created_at) VALUES(?,?)", (message, utc_now())
            )

    def summary_errors(self):
        with self._lock:
            return [
                dict(row)
                for row in self._db.execute("SELECT * FROM summary_error ORDER BY id").fetchall()
            ]

    def set_status(self, status: str, *, ended=False):
        with self._lock, self._db:
            self._db.execute(
                "UPDATE meeting SET status=?, ended_at=COALESCE(?,ended_at)",
                (status, utc_now() if ended else None),
            )
        self.export()

    def rename(self, title: str):
        title = title.strip()
        if not title or "\n" in title or "\r" in title:
            raise ValueError("会议名称须为非空单行文字")
        with self._lock:
            with self._db:
                self._db.execute("UPDATE meeting SET title=?", (title,))
            self.export()
            return self.metadata

    def content(self, kind="all"):
        """Canonical readable text for clipboard and disk, including every confirmed entry."""
        if kind not in {"all", "transcript", "summaries"}:
            raise ValueError("不支持的会议内容类型")
        with self._lock:
            meta = self.metadata
            parts = [f"# {meta['title']}\n\n开始时间：{meta['started_at']}"]
            if kind in {"all", "transcript"}:
                parts.append("## 会议转写\n\n" + "\n\n".join(e.markdown() for e in self.entries()))
            if kind in {"all", "summaries"}:
                parts.append(
                    "## 分时段总结\n\n"
                    + "\n\n".join(
                        f"### {timestamp(s.start_seconds)}–{timestamp(s.end_seconds)}\n\n{s.text}"
                        for s in self.summaries()
                    )
                )
            return "\n\n".join(parts) + "\n"

    def context_snapshot(self):
        with self._lock:
            return {
                "meeting_id": self.metadata["id"],
                "through_entry": self.last_entry,
                "content": self.content(),
            }

    def export(self):
        with self._lock:
            meta = self.metadata
            transcript = self.content("transcript")
            notes = self.content("summaries")
            for name, text in [
                ("transcript.md", transcript),
                ("summaries.md", notes),
                ("meeting.json", json.dumps(meta, ensure_ascii=False, indent=2)),
            ]:
                temporary = self.directory / (name + ".tmp")
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(self.directory / name)
            return self.directory

    def close(self):
        with self._lock:
            self._db.close()


def list_meetings(root: Path):
    meetings = []
    for path in sorted(Path(root).glob("*/meeting.json"), reverse=True):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            meetings.append({**meta, "directory": str(path.parent)})
        except (OSError, ValueError):
            continue
    return meetings
