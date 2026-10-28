"""Real Qwen verification through Python APIs only; no server or audio devices."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import soundfile as sf
from samples import public_sample

from livecaptions_asr import AudioDecoder, Recognizer
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine


def main():
    recognizer = Recognizer(
        NativeQwenEngine(
            NativeQwenOptions(
                Path("models/native/qwen3-asr-0.6b-q8_0.gguf"),
                Path("runtime/native/win-x64/crispasr.dll"),
            )
        )
    )
    recognizer.prepare()
    report = {"transport": "python", "capture": False, "samples": {}}
    for language in ("zh", "en"):
        sample = public_sample(language)
        first_chunk = next(AudioDecoder().chunks(sample))
        single = recognizer.recognize(first_chunk.audio, language)
        assert single.text and single.device.startswith("Vulkan")
        progress = []
        result = recognizer.transcribe_file(sample, language, on_segment=progress.append)
        assert result.text and result.device.startswith("Vulkan")
        stream = recognizer.create_stream(language)
        events = []
        for chunk in AudioDecoder().chunks(sample):
            for offset in range(0, len(chunk.audio.samples), 16000):
                events.extend(stream.feed(chunk.audio.samples[offset : offset + 16000]))
                assert stream.buffered_seconds <= 8.3
        events.extend(stream.finish())
        finals = [event.segment for event in events if event.kind == "final"]
        assert finals and any(event.kind == "partial" for event in events)
        assert any(event.kind == "sentence" for event in events)
        assert abs(stream.duration_seconds - result.duration_seconds) < 0.01
        if language == "zh":
            assert "交易" in result.text and any("交易" in segment.text for segment in finals)
        report["samples"][language] = {
            "single_window": single.to_dict(),
            "file": result.to_dict(),
            "progress_count": len(progress),
            "events": [event.to_dict() for event in events],
            "buffered_after_finish": stream.buffered_seconds,
        }
        print(
            json.dumps(
                {
                    "language": language,
                    "file_chunks": result.chunks_completed,
                    "partial_updates": sum(event.kind == "partial" for event in events),
                    "final_segments": len(finals),
                    "device": result.device,
                }
            ),
            flush=True,
        )
    long_file = Path("data/samples/long-public-zh.wav")
    if not long_file.is_file():
        speech, rate = sf.read(public_sample("zh"), dtype="float32")
        with sf.SoundFile(long_file, "w", samplerate=rate, channels=1) as target:
            for _ in range(32):
                target.write(speech)
                target.write(np.zeros(round(rate * 0.4), dtype="float32"))
    progress = []
    long_result = recognizer.transcribe_file(long_file, "zh", on_segment=progress.append)
    assert long_result.duration_seconds > 120 and long_result.chunks_completed > 1
    assert len(progress) == long_result.chunks_completed and "交易" in long_result.text
    assert all(
        left.end_seconds == right.start_seconds for left, right in pairwise(long_result.segments)
    )
    assert long_result.segments[0].start_seconds == 0
    assert long_result.segments[-1].end_seconds == long_result.duration_seconds
    report["long_file"] = long_result.to_dict()
    print(
        json.dumps(
            {
                "long_seconds": long_result.duration_seconds,
                "chunks": long_result.chunks_completed,
                "progress": len(progress),
            }
        ),
        flush=True,
    )
    report["model"] = asdict(recognizer.status())
    assert report["model"]["load_count"] == 1
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(f"data/verification/core-api-{stamp}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output}")

    recognizer.close()


if __name__ == "__main__":
    main()
