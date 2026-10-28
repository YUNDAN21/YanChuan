"""Text-only subtitle cue splitting; consumes ASR punctuation, never loads a model."""

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from .domain import ASRError
from .results import Serializable


@dataclass(frozen=True)
class SubtitleCue(Serializable):
    start: int
    end: int
    text: str
    boundary: str


_CONNECTORS = re.compile(
    r"\b(?:and|but|because|although|however|therefore|while|when|which|so that)\b"
    r"|但是|不过|因为|所以|然后|而且|并且|同时|接下来|如果|虽然"
    r"|しかし|けれど|それから|そして|そのため",
    re.IGNORECASE,
)
_CLOSERS = set("”’」』）》】)]}")
_OPENERS = set("“‘「『（《【([{")
_ENDING = set("。！？!?；;\n")
_CLAUSE = set("，、,:：")
_ABBREVIATION = re.compile(r"\b(?:mr|mrs|ms|dr|prof|sr|jr|vs|etc|e\.g|i\.e)\.$", re.IGNORECASE)


def _units(text: str) -> int:
    return sum(0 if unicodedata.combining(char) else 1 if char.isascii() else 2 for char in text)


def _safe_cut(text: str, index: int) -> bool:
    if index == len(text):
        return True
    before, after = text[index - 1], text[index]
    if (
        after in _CLOSERS
        or after in _ENDING
        or after in _CLAUSE
        or after == "."
        or before in _OPENERS
    ):
        return False
    if (
        unicodedata.combining(after)
        or after in "\ufe0e\ufe0f\u200d"
        or before == "\u200d"
        or "\U0001f3fb" <= after <= "\U0001f3ff"
    ):
        return False
    return not (before.isascii() and after.isascii() and before.isalnum() and after.isalnum())


class SubtitleSplitter:
    """Return source-preserving cues at sentence/clause/phrase/word boundaries.

    max_units is a readable text budget (CJK characters count as two). An optional
    width measurement imposes the actual rendering limit without importing a UI.
    Character cuts are used only when no suitable text boundary fits.
    """

    def __init__(self, max_units: int = 48):
        if isinstance(max_units, bool) or not isinstance(max_units, int) or max_units < 4:
            raise ASRError("invalid_options", "字幕长度预算必须为不小于 4 的整数。")
        self.max_units = max_units

    def split(
        self,
        text: str,
        *,
        max_width: float | None = None,
        measure: Callable[[str], float] | None = None,
    ) -> tuple[SubtitleCue, ...]:
        if (max_width is None) != (measure is None):
            raise ASRError("invalid_options", "字幕宽度与测量函数须同时提供。")
        if max_width is not None and (not math.isfinite(max_width) or max_width <= 0):
            raise ASRError("invalid_options", "字幕可用宽度必须为有限正数。")
        if not text.strip():
            return ()
        connectors = {match.start() for match in _CONNECTORS.finditer(text) if match.start() > 0}
        cuts = {}
        for end in range(1, len(text) + 1):
            char = text[end - 1]
            terminal = char in _ENDING
            if char == ".":
                decimal = (
                    end < len(text) and text[end - 2 : end - 1].isdigit() and text[end].isdigit()
                )
                terminal = not decimal and not _ABBREVIATION.search(text[:end])
            reason = "sentence" if terminal else "clause" if char in _CLAUSE else None
            if reason:
                cut = end
                while cut < len(text) and text[cut] in _CLOSERS:
                    cut += 1
                while cut < len(text) and text[cut] in " \t":
                    cut += 1
                cuts[cut] = reason
        result = []
        start = 0

        def fits(end):
            value = text[start:end].strip()
            return _units(value) <= self.max_units and (
                measure is None or measure(value) <= max_width
            )

        while start < len(text):
            limit = start
            for end in range(start + 1, len(text) + 1):
                if fits(end):
                    limit = end
                else:
                    break
            if limit == start:
                raise ASRError("subtitle_width_too_small", "字幕可用宽度不足以容纳一个字符。")
            if limit == len(text):
                sentences = [
                    end
                    for end, reason in cuts.items()
                    if start < end <= limit and reason == "sentence"
                ]
                end = min(sentences) if sentences else limit
                reason = cuts.get(end, "end")
            else:
                sentences = [
                    end
                    for end, reason in cuts.items()
                    if start < end <= limit and reason == "sentence"
                ]
                clauses = [
                    end
                    for end, reason in cuts.items()
                    if start < end <= limit and reason == "clause"
                ]
                phrases = [
                    end
                    for end in connectors
                    if start < end <= limit and _units(text[start:end]) >= self.max_units // 4
                ]
                words = [
                    end
                    for end in range(start + 1, limit + 1)
                    if text[end - 1].isspace() and _safe_cut(text, end)
                ]
                if sentences:
                    end, reason = min(sentences), "sentence"
                elif clauses:
                    end, reason = max(clauses), "clause"
                elif phrases:
                    end, reason = max(phrases), "phrase"
                elif words:
                    end, reason = max(words), "word"
                else:
                    candidates = [
                        end for end in range(start + 1, limit + 1) if _safe_cut(text, end)
                    ]
                    end, reason = (
                        (max(candidates) if candidates else max(start + 1, limit)),
                        "width",
                    )
            result.append(SubtitleCue(start, end, text[start:end], reason))
            start = end
        return tuple(result)
