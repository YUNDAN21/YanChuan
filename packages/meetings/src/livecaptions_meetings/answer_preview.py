"""Extract the top-level answer string from a partial JSON model response."""

import re
from json import JSONDecoder

_TEXT_PIECE = re.compile(r'[^"\\\x00-\x1f]+|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')


def answer_preview(source, *, require_action=False):
    """Decode complete string fragments. Final validation stays with MeetingAgent."""
    source = source.lstrip()
    if source.startswith("```"):
        header, newline, source = source.partition("\n")
        if not newline or header.strip().lower() not in {"```", "```json"}:
            return ""
        source = source.lstrip()
    if not source.startswith("{"):
        return ""

    decoder = JSONDecoder()
    text, action = "", None
    remaining = source[1:].lstrip()
    while remaining.startswith('"'):
        try:
            key, index = decoder.raw_decode(remaining)
            value = remaining[index:].lstrip()
            if not value.startswith(":"):
                break
            value = value[1:].lstrip()
            if key == "text" and value.startswith('"'):
                end = 1
                while match := _TEXT_PIECE.match(value, end):
                    end = match.end()
                text, _ = decoder.raw_decode(value[:end] + '"')
                # A channel can split a Unicode escape pair between chunks.
                text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "ignore")
                index = end + 1 if value[end : end + 1] == '"' else end
            else:
                item, index = decoder.raw_decode(value)
                if key == "action":
                    action = item
        except ValueError:
            break
        remaining = value[index:].lstrip()
        if not remaining.startswith(","):
            break
        remaining = remaining[1:].lstrip()
    return text if not require_action or action == "answer" else ""
