import math
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import BinaryIO

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .domain import ASRError, AudioData

AudioSource = str | PathLike | BinaryIO


@dataclass(frozen=True)
class AudioChunk:
    start: float
    end: float
    audio: AudioData
    boundary: str


class AudioDecoder:
    """Bounded local WAV/FLAC decoder, independent of transports and capture."""

    def __init__(self, max_bytes: int | None = None, max_seconds: float | None = None):
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds

    def chunks(self, source: AudioSource, chunk_seconds: float = 20):
        """Read bounded windows and prefer a quiet boundary near their end.

        No whole-file read or resampling. Supplied handles remain owned by the
        caller; local paths are opened by this iterator.
        """
        try:
            if not np.isfinite(chunk_seconds) or chunk_seconds < 0.1:
                raise ASRError("invalid_options", "文件识别窗口至少需要 0.1 秒。")
            if isinstance(source, (str, PathLike)):
                source = Path(source)
                size = source.stat().st_size
            else:
                source.seek(0, 2)
                size = source.tell()
                source.seek(0)
            if not size:
                raise ASRError("empty_audio", "音频文件为空。")
            if self.max_bytes is not None and size > self.max_bytes:
                raise ASRError("upload_too_large", "音频文件超过上传大小限制。")
            with sf.SoundFile(source) as reader:
                if reader.format not in {"WAV", "WAVEX", "FLAC", "RF64"}:
                    raise ASRError("unsupported_audio", "目前支持 WAV 和 FLAC。")
                rate = reader.samplerate
                if not 8000 <= rate <= 192000 or not 1 <= reader.channels <= 8:
                    raise ASRError("invalid_audio", "采样率或声道数量不受支持。")
                duration = reader.frames / rate
                if duration < 0.1:
                    raise ASRError("audio_too_short", "音频至少需要 0.1 秒。")
                if self.max_seconds is not None and duration > self.max_seconds:
                    raise ASRError("audio_too_long", "音频时长超过配置上限。")
                position = 0
                while position < reader.frames:
                    reader.seek(position)
                    count = min(round(chunk_seconds * rate), reader.frames - position)
                    if reader.frames - position - count < rate // 10:
                        count = reader.frames - position
                    samples = reader.read(count, dtype="float32", always_2d=True).mean(axis=1)
                    if not np.isfinite(samples).all():
                        raise ASRError("invalid_audio", "音频包含非有限数值。")
                    if rate != 16000:
                        factor = math.gcd(rate, 16000)
                        samples = resample_poly(samples, 16000 // factor, rate // factor)
                    boundary = "end" if position + count >= reader.frames else "max_duration"
                    used = count
                    if boundary != "end" and len(samples) >= 32000:
                        # 100 ms RMS frames; choose the latest quiet frame in the last 20%.
                        frame_size = 1600
                        begin = int(len(samples) * 0.8) // frame_size
                        ends = list(range(begin, len(samples) // frame_size))
                        quiet = [
                            index
                            for index in ends
                            if float(
                                np.sqrt(
                                    np.mean(
                                        samples[index * frame_size : (index + 1) * frame_size] ** 2
                                    )
                                )
                            )
                            < 0.003
                        ]
                        if quiet:
                            cut = (quiet[-1] + 1) * frame_size
                            used = min(count, round(cut / 16000 * rate))
                            samples = samples[: round(used / rate * 16000)]
                            boundary = "pause"
                    end = position + used
                    audio = AudioData.from_pcm16k(samples, used / rate)
                    yield AudioChunk(position / rate, end / rate, audio, boundary)
                    position = end
        except ASRError:
            raise
        except (OSError, RuntimeError, ValueError, sf.LibsndfileError) as exc:
            raise ASRError("invalid_audio", "无法解码音频，请提供有效的 WAV 或 FLAC。") from exc
