"""Bounded GGUF structural validation for the supported CrispASR Qwen models."""

import math
import struct
from pathlib import Path

from ..domain import ASRError

_SCALARS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}


def inspect_qwen_gguf(path: Path):
    try:
        size = path.stat().st_size
        with path.open("rb") as file:

            def read(n):
                data = file.read(n)
                if len(data) != n:
                    raise ValueError("truncated metadata")
                return data

            def number(fmt):
                return struct.unpack("<" + fmt, read(struct.calcsize(fmt)))[0]

            def string(keep=True):
                length = number("Q")
                if length > 16 * 1024 * 1024 or file.tell() + length > size:
                    raise ValueError("invalid string size")
                if keep:
                    return read(length).decode("utf-8")
                file.seek(length, 1)

            def value(kind, keep=False, depth=0):
                if kind in _SCALARS:
                    return number(_SCALARS[kind])
                if kind == 8:
                    return string(keep)
                if kind == 9 and depth == 0:
                    element, count = number("I"), number("Q")
                    if count > 2_000_000:
                        raise ValueError("invalid array size")
                    if element in _SCALARS:
                        length = struct.calcsize(_SCALARS[element]) * count
                        if file.tell() + length > size:
                            raise ValueError("truncated array")
                        file.seek(length, 1)
                    else:
                        for _ in range(count):
                            value(element, depth=1)
                    return None
                raise ValueError("unsupported metadata type")

            if read(4) != b"GGUF" or number("I") != 3:
                raise ValueError("requires GGUF v3")
            count, metadata_count = number("Q"), number("Q")
            if not 1 <= count <= 2000 or not 1 <= metadata_count <= 1000:
                raise ValueError("invalid metadata count")
            metadata = {}
            for _ in range(metadata_count):
                key = string()
                kind = number("I")
                keep = key in {
                    "general.architecture",
                    "general.file_type",
                    "general.alignment",
                    "qwen3asr.llm.d_model",
                }
                item = value(kind, keep)
                if keep:
                    metadata[key] = item
            if metadata.get("general.architecture") != "qwen3asr":
                raise ValueError("requires CrispASR qwen3asr layout")
            precision = {1: "f16", 7: "q8_0"}.get(metadata.get("general.file_type"))
            if precision is None:
                raise ValueError("requires F16 or Q8_0")
            model_size = {1024: "0.6B", 2048: "1.7B"}.get(metadata.get("qwen3asr.llm.d_model"))
            if model_size is None:
                raise ValueError("requires Qwen3-ASR 0.6B or 1.7B")
            ends, names = [], set()
            for _ in range(count):
                name = string()
                dims = number("I")
                if not 1 <= dims <= 4 or name in names:
                    raise ValueError("invalid tensor")
                shape = [number("Q") for _ in range(dims)]
                elements = math.prod(shape)
                if any(n < 1 for n in shape) or elements > 2**34:
                    raise ValueError("invalid shape")
                kind, offset = number("I"), number("Q")
                if kind not in {0, 1, 8, 30} or (kind == 8 and elements % 32):
                    raise ValueError("unsupported tensor type")
                length = elements * ({0: 4, 1: 2, 30: 2}.get(kind, 0))
                if kind == 8:
                    length = elements // 32 * 34
                ends.append(offset + length)
                names.add(name)
            alignment = metadata.get("general.alignment", 32)
            if not isinstance(alignment, int) or alignment < 1 or alignment > 4096:
                raise ValueError("invalid alignment")
            start = (file.tell() + alignment - 1) // alignment * alignment
            if start + max(ends) > size:
                raise ValueError("truncated weights")
            if not {"audio.mel_filters", "audio.mel_window", "token_embd.weight"} <= names:
                raise ValueError("missing required tensors")
            return {
                "architecture": "qwen3asr",
                "precision": precision,
                "model_size": model_size,
                "tensors": count,
                "bytes": size,
            }
    except (OSError, ValueError, struct.error, UnicodeError) as exc:
        raise ASRError("invalid_model", f"不兼容或不完整的 Qwen GGUF：{exc}") from exc
