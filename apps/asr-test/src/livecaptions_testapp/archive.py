"""Bounded I/O for mixed audio, continuous source volumes and sentence WAV clips."""

import json
import struct
import threading
from pathlib import Path

import numpy as np

RATE = 16000


def wav_header(frames: int) -> bytes:
    size = frames * 2
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
    if size <= 0xFFFFFFFF - 36:
        return (
            b"RIFF"
            + struct.pack("<I", 36 + size)
            + b"WAVE"
            + fmt
            + b"data"
            + struct.pack("<I", size)
        )
    return (
        b"RF64\xff\xff\xff\xffWAVEds64"
        + struct.pack("<IQQQI", 28, 72 + size, size, frames, 0)
        + fmt
        + b"data\xff\xff\xff\xff"
    )


class AudioArchive:
    """PCM source volumes cover the entire timeline, including all silence.

    Headers/frames are kept in the manifest so active volumes can be read safely
    before closing. Sentence WAV clips are additional, bounded exports.
    """

    def __init__(self, directory: Path, chunk_seconds: float = 30):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.chunk_frames = round(chunk_seconds * RATE)
        self.frames = 0
        self.parts: list[dict] = []
        self.sections: list[dict] = []
        self.closed = False
        self._file = None
        self._lock = threading.RLock()
        self._manifest_frames = 0
        self._manifest()

    @property
    def duration(self):
        return self.frames / RATE

    def append(self, samples: np.ndarray):
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        with self._lock:
            if self.closed:
                raise OSError("Audio archive is closed")
            offset = 0
            while offset < len(pcm):
                if self._file is None:
                    name = f"source-{len(self.parts):06d}.pcm"
                    self._file = (self.directory / name).open("wb")
                    self.parts.append({"file": name, "start_frame": self.frames, "frames": 0})
                part = self.parts[-1]
                count = min((self.chunk_frames - part["frames"]) * 2, len(pcm) - offset)
                self._file.write(pcm[offset : offset + count])
                self._file.flush()
                part["frames"] += count // 2
                self.frames += count // 2
                offset += count
                if part["frames"] == self.chunk_frames:
                    self._file.close()
                    self._file = None
            if self.frames - self._manifest_frames >= RATE * 5:
                self._manifest()

    def samples(self, start: float, end: float) -> np.ndarray:
        begin, finish = round(start * RATE), round(end * RATE)
        with self._lock:
            if begin < 0 or finish > self.frames:
                raise ValueError("Archive range outside written audio")
            output = np.zeros(max(0, finish - begin), dtype=np.float32)
            for part in self.parts:
                left = max(begin, part["start_frame"])
                right = min(finish, part["start_frame"] + part["frames"])
                if left < right:
                    with (self.directory / part["file"]).open("rb") as source:
                        source.seek((left - part["start_frame"]) * 2)
                        data = source.read((right - left) * 2)
                    output[left - begin : right - begin] = np.frombuffer(data, dtype="<i2") / 32768
            return output

    def iter_pcm(self, begin=0, finish=None):
        with self._lock:
            parts = [dict(part) for part in self.parts]
            finish = self.frames * 2 if finish is None else finish
        for part in parts:
            left = max(begin, part["start_frame"] * 2)
            right = min(finish, (part["start_frame"] + part["frames"]) * 2)
            if left >= right:
                continue
            with (self.directory / part["file"]).open("rb") as source:
                source.seek(left - part["start_frame"] * 2)
                remaining = right - left
                while remaining:
                    data = source.read(min(65536, remaining))
                    if not data:
                        raise OSError("Archive volume is truncated")
                    yield data
                    remaining -= len(data)

    def iter_wav(self, begin=0, finish=None):
        header = wav_header(self.frames)
        finish = len(header) + self.frames * 2 if finish is None else finish
        if begin < len(header):
            yield header[begin : min(finish, len(header))]
        if finish > len(header):
            yield from self.iter_pcm(max(0, begin - len(header)), finish - len(header))

    def section(self, group: dict):
        begin = round(group["start_seconds"] * RATE)
        finish = min(self.frames, round(group["end_seconds"] * RATE))
        name = f"sentence-{group['id']:06d}.wav"
        if finish > begin:
            with (self.directory / name).open("wb") as target:
                target.write(wav_header(finish - begin))
                for data in self.iter_pcm(begin * 2, finish * 2):
                    target.write(data)
        else:
            name = None
        with self._lock:
            self.sections.append({**group, "audio_file": name})
            self._manifest()

    def finish(self):
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None
            self.closed = True
            self._manifest()

    def _manifest(self):
        value = {
            "format": "pcm_s16le",
            "sample_rate": RATE,
            "channels": 1,
            "frames": self.frames,
            "duration_seconds": self.duration,
            "closed": self.closed,
            "source_parts": self.parts,
            "semantic_sections": self.sections,
        }
        temporary = self.directory / "manifest.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "manifest.json")
        self._manifest_frames = self.frames
