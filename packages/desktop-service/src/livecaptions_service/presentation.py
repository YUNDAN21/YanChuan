"""Event projection and semantic line wrapping, independent of Qt and devices."""

from collections.abc import Callable
from dataclasses import dataclass

from livecaptions_asr.results import Segment, StreamEvent
from livecaptions_asr.subtitles import SubtitleSplitter


@dataclass(frozen=True)
class CaptionEntry:
    key: str
    text: str
    start_seconds: float
    pending: bool = False


def join_text(parts: list[str]) -> str:
    result = ""
    for part in parts:
        part = part.strip()
        if part:
            space = " " if result and result[-1].isascii() and part[0].isascii() else ""
            result += space + part
    return result


class CaptionHistory:
    """Only sentence events commit history; revisions replace their segment ID."""

    def __init__(self):
        self.confirmed: list[CaptionEntry] = []
        self._session = 0
        self.begin_session()

    def begin_session(self):
        self._session += 1
        self._segments: dict[int, Segment] = {}
        self._retired: set[int] = set()
        self._sentences: set[int] = set()

    def apply(self, event: StreamEvent):
        if event.sentence is not None:
            sentence = event.sentence
            if sentence.id in self._sentences:
                return
            self._sentences.add(sentence.id)
            self._retired.update(sentence.segment_ids)
            for key in sentence.segment_ids:
                self._segments.pop(key, None)
            if sentence.text.strip():
                self.confirmed.append(
                    CaptionEntry(
                        f"{self._session}:{sentence.id}",
                        sentence.text.strip(),
                        sentence.start_seconds,
                    )
                )
        elif event.segment is not None:
            segment = event.segment
            old = self._segments.get(segment.id)
            if segment.id not in self._retired and (
                old is None or segment.revision >= old.revision
            ):
                self._segments[segment.id] = segment

    @property
    def pending(self) -> CaptionEntry | None:
        segments = sorted(self._segments.values(), key=lambda item: (item.start_seconds, item.id))
        text = join_text([segment.text for segment in segments])
        return CaptionEntry("pending", text, segments[0].start_seconds, True) if text else None


def semantic_lines(text: str, width: float, measure: Callable[[str], float]) -> list[str]:
    """Shared subtitle cue rules, constrained by the caller's actual font width."""
    if width <= 0:
        return [text.strip()] if text.strip() else []
    return [
        cue.text.strip()
        for cue in SubtitleSplitter().split(text, max_width=width, measure=measure)
        if cue.text.strip()
    ]


def compact_lines(
    history: CaptionHistory, width: float, measure: Callable[[str], float]
) -> list[str]:
    entries = history.confirmed[-2:] + ([history.pending] if history.pending else [])
    lines = [line for entry in entries for line in semantic_lines(entry.text, width, measure)]
    return ([""] * 2 + lines)[-2:]
