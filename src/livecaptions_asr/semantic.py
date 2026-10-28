"""Text-assisted sentence grouping, independent of capture and inference.

This is a punctuation/pause/continuation heuristic, not a topic classification LLM.
Recognition windows can be short while archived sentences span several windows.
"""

import re

from .results import Segment, Sentence


class SemanticGrouper:
    def __init__(self, max_seconds=60):
        self.max_seconds = max_seconds
        self.pending: list[Segment] = []
        self.next_id = 0

    @staticmethod
    def complete(text: str) -> bool:
        text = text.strip()
        if not re.search(r"[。！？.!?][\"'”’」』）)]*$", text):
            return False
        core = re.sub(r"[。！？.!?\"'”’」』）)]+$", "", text).strip()
        if re.search(
            r"\b(?:and|but|because|if|although|however|dr|mr|mrs|e\.g)\.?$", core, re.IGNORECASE
        ):
            return False
        if core.endswith(
            ("因为", "但是", "如果", "以及", "然后", "所以", "そして", "けれど", "ので")
        ):
            return False
        return all(
            text.count(left) <= text.count(right)
            for left, right in [("（", "）"), ("(", ")"), ("「", "」"), ("“", "”")]
        )

    def add(self, segment: Segment) -> list[Sentence]:
        if not segment.text.strip():
            return []
        emitted = []
        if self.pending and segment.start_seconds - self.pending[-1].end_seconds >= 1.5:
            emitted.append(self._flush("long_pause"))
        self.pending.append(segment)
        span = self.pending[-1].end_seconds - self.pending[0].start_seconds
        if span >= self.max_seconds:
            emitted.append(self._flush("max_duration"))
        elif segment.boundary == "semantic_endpoint":
            emitted.append(self._flush("semantic_endpoint"))
        elif segment.boundary != "max_duration" and self.complete(segment.text):
            emitted.append(self._flush("sentence_and_pause"))
        return emitted

    def flush(self) -> list[Sentence]:
        return [self._flush("end")] if self.pending else []

    def _flush(self, reason: str) -> Sentence:
        parts = self.pending
        text = ""
        for part in parts:
            value = part.text.strip()
            separator = " " if text and text[-1:].isascii() and value[:1].isascii() else ""
            text += separator + value
        result = Sentence(
            self.next_id,
            parts[0].start_seconds,
            parts[-1].end_seconds,
            text,
            tuple(part.id for part in parts),
            reason,
        )
        self.next_id += 1
        self.pending = []
        return result
