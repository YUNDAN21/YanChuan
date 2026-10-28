"""Silent native long-file and real-time-paced PCM validation through the core APIs."""

import argparse
import itertools
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

from livecaptions_asr import (
    AudioDecoder,
    ModelSegmenter,
    Recognizer,
    SileroVADModel,
    SmartTurnEndpoint,
)
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--size", choices=("0.6", "1.7"), required=True)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.root.resolve()
    frames = args.minutes * 60 * 16000
    # Stability does not need labels: use the existing public three-language samples,
    # independently of the larger reference corpus used for accuracy measurements.
    corpus = {
        "samples": [{"path": f"data/samples/asr_{language}.wav"} for language in ("zh", "en", "ja")]
    }
    source = args.output / "stability.wav"
    remaining = frames
    with sf.SoundFile(source, "w", samplerate=16000, channels=1, subtype="PCM_16") as output:
        for sample in itertools.cycle(corpus["samples"]):
            pcm = np.concatenate(
                [chunk.audio.samples for chunk in AudioDecoder().chunks(root / sample["path"])]
            )
            block = np.concatenate((pcm, np.zeros(16000, dtype=np.float32)))[:remaining]
            output.write(block)
            remaining -= len(block)
            if not remaining:
                break
    engine = NativeQwenEngine(
        NativeQwenOptions(
            root / f"models/native/qwen3-asr-{args.size}b-q8_0.gguf",
            root / "runtime/native/win-x64/crispasr.dll",
            model_id=f"Qwen/Qwen3-ASR-{args.size}B",
        )
    )
    recognizer = Recognizer(engine)
    report = {
        "size": args.size,
        "capture": False,
        "playback": False,
        "audio_seconds": args.minutes * 60,
        "repeated_corpus_for_stability_only": True,
        "complete": False,
    }
    records = args.output / "progress.jsonl"
    process = psutil.Process()

    def record(value):
        value["rss"] = process.memory_info().rss
        with records.open("a", encoding="utf-8") as log:
            log.write(json.dumps(value, ensure_ascii=False) + "\n")
        print(json.dumps(value), flush=True)

    try:
        recognizer.prepare()
        vad = SileroVADModel.from_path(root / "models/endpoint/silero_vad.onnx")
        endpoint = SmartTurnEndpoint.from_path(root / "models/endpoint/smart-turn-v3.2-cpu.onnx")
        vad.prepare()
        endpoint.prepare()
        stream = recognizer.create_stream(segmenter=ModelSegmenter(vad.create_detector(), endpoint))
        samples, latencies, lag, final_ends, rss = 0, [], [], [], []
        started = time.monotonic()
        with sf.SoundFile(source) as audio:
            while len(block := audio.read(16000, dtype="float32")):
                # Advance the producer by one second, regardless of inference duration.
                target = started + (samples + len(block)) / 16000
                time.sleep(max(0, target - time.monotonic()))
                begin = time.monotonic()
                events = list(stream.feed(block))
                latencies.append(time.monotonic() - begin)
                samples += len(block)
                lag.append(max(0, time.monotonic() - target))
                assert stream.buffered_seconds <= 17.5
                for event in events:
                    if event.kind == "final":
                        final_ends.append(event.segment.end_seconds)
                if samples % (60 * 16000) == 0:
                    rss.append(process.memory_info().rss)
                    record(
                        {
                            "phase": "live",
                            "audio_seconds": samples / 16000,
                            "lag_seconds": lag[-1],
                            "buffer_seconds": stream.buffered_seconds,
                            "finals": len(final_ends),
                        }
                    )
        for event in stream.finish():
            if event.kind == "final":
                final_ends.append(event.segment.end_seconds)
        assert final_ends and final_ends == sorted(final_ends)
        assert stream.closed and abs(stream.duration_seconds - args.minutes * 60) < 0.01
        report["live"] = {
            "wall_seconds": time.monotonic() - started,
            "median_step_seconds": statistics.median(latencies),
            "p95_step_seconds": float(np.percentile(latencies, 95)),
            "max_lag_seconds": max(lag),
            "final_lag_seconds": lag[-1],
            "max_lag_last_minute": max(lag[-60:]),
            "finals": len(final_ends),
            "rss_per_minute": rss,
        }
        # A queue that continues growing fails; short scheduling spikes are recorded separately.
        assert max(lag[-60:]) < 5, "Live producer backlog did not drain"
        if len(rss) >= 10:
            baseline = statistics.median(rss[4:9])
            assert max(rss[-5:]) - baseline < max(256 * 1024**2, baseline * 0.25)
        started = time.monotonic()
        file_result = recognizer.transcribe_file(
            source,
            on_segment=lambda item: record(
                {
                    "phase": "file",
                    "audio_seconds": item.processed_seconds,
                }
            ),
        )
        report["file"] = {
            "wall_seconds": time.monotonic() - started,
            "duration_seconds": file_result.duration_seconds,
            "chunks": file_result.chunks_completed,
            "text_nonempty": bool(file_result.text),
        }
        assert file_result.chunks_completed > 1 and file_result.text
        assert abs(file_result.duration_seconds - args.minutes * 60) < 0.01
        assert engine.status().load_count == 1
        report["status"] = asdict(engine.status())
        report["complete"] = True
    finally:
        recognizer.close()
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )


if __name__ == "__main__":
    main()
