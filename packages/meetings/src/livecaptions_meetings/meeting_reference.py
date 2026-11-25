"""Immutable, paged meeting source; no model, network, filesystem or UI access."""

import json
import unicodedata
from dataclasses import asdict, dataclass

from .chat_context import text_tokens


@dataclass(frozen=True)
class ReferenceChunk:
    id: int
    entry_id: int
    start_seconds: float
    end_seconds: float
    text: str


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


class MeetingReference:
    """Stable IDs allow evicted text to be read again from the same request snapshot."""

    def __init__(self, meeting_id, title, entries):
        self.meeting_id, self.title = meeting_id, title
        entries = tuple(entries)
        self.through_entry = entries[-1].id if entries else 0
        self.chunks = tuple(
            ReferenceChunk(index, entry.id, entry.start_seconds, entry.end_seconds, text)
            for index, (entry, text) in enumerate(
                (entry, entry.text[offset : offset + 600])
                for entry in entries
                for offset in range(0, len(entry.text), 600)
            )
        )
        self.source_tokens = sum(text_tokens(c.text) for c in self.chunks)

    def overview(self):
        return json.dumps(
            {
                "title": self.title[:200],
                "chunks": len(self.chunks),
                "through_entry": self.through_entry,
                "duration_seconds": max((c.end_seconds for c in self.chunks), default=0),
                "source": "本地完整会议原文，可按片段编号搜索或读取；只包含提交问题时已确认的内容。",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _page(cursor, limit):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("无效的原文分页参数")

    def directory(self, cursor=0, limit=8):
        self._page(cursor, limit)
        chunks = self.chunks[cursor : cursor + limit]
        return {
            "items": [{**asdict(c), "text": c.text[:80]} for c in chunks],
            "next": cursor + limit if cursor + limit < len(self.chunks) else None,
            "total": len(self.chunks),
        }

    def read(self, cursor=0, limit=1):
        self._page(cursor, limit)
        chunks = self.chunks[cursor : cursor + limit]
        return {
            "items": [asdict(c) for c in chunks],
            "next": cursor + limit if cursor + limit < len(self.chunks) else None,
            "total": len(self.chunks),
        }

    def search(self, terms, cursor=0, limit=8):
        self._page(cursor, limit)
        if (
            not isinstance(terms, list)
            or not 1 <= len(terms) <= 8
            or any(not isinstance(t, str) or not t.strip() or len(t) > 80 for t in terms)
        ):
            raise ValueError("搜索需要一至八个有效关键词")
        terms = [normalized(t.strip()) for t in terms]
        matches = []
        for index, chunk in enumerate(self.chunks):
            text = chunk.text
            if index + 1 < len(self.chunks) and self.chunks[index + 1].entry_id == chunk.entry_id:
                text += self.chunks[index + 1].text[:80]
            if any(term in normalized(text) for term in terms):
                matches.append(chunk)
        return {
            "items": [
                {**asdict(c), "text": c.text[:120]} for c in matches[cursor : cursor + limit]
            ],
            "next": cursor + limit if cursor + limit < len(matches) else None,
            "matches": len(matches),
            "hint": "命中仅供定位，请读取完整片段及相邻片段，核对后续修订。",
        }
