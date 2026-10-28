"""Serial real-model accuracy/performance evaluation against the pinned public corpus."""

import argparse
import json
import statistics
import sys
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf
from rapidfuzz.distance import Levenshtein

from livecaptions_asr import AudioData, Recognizer


def normalize(text, language):
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    return text.split() if language == "en" else list("".join(text.split()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--engine", choices=("native",), default="native")
    parser.add_argument("--size", choices=("0.6", "1.7"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    from livecaptions_asr.engines.native_options import NativeQwenOptions
    from livecaptions_asr.engines.native_qwen import NativeQwenEngine

    engine = NativeQwenEngine(
        NativeQwenOptions(
            root / f"models/native/qwen3-asr-{args.size}b-q8_0.gguf",
            root / "runtime/native/win-x64/crispasr.dll",
            model_id=f"Qwen/Qwen3-ASR-{args.size}B",
        )
    )
    from benchmark_native import GpuMemory

    corpus = json.loads((root / "work/native-migration/corpus.json").read_text("utf-8"))
    recognizer = Recognizer(engine)
    report = {
        "engine": args.engine,
        "size": args.size,
        "corpus_revision": corpus["revision"],
        "playback": False,
        "capture": False,
        "samples": [],
        "languages": {},
        "complete": False,
    }
    with (
        GpuMemory() as memory,
        (args.output / "samples.jsonl").open("w", encoding="utf-8") as records,
    ):
        started = time.perf_counter()
        recognizer.prepare()
        report["prepare_seconds"] = time.perf_counter() - started
        report["status"] = asdict(engine.status())
        first = corpus["samples"][0]
        pcm, rate = sf.read(root / first["path"], dtype="float32")
        assert rate == 16000
        started = time.perf_counter()
        recognizer.recognize(AudioData.from_pcm16k(pcm), first["language"])
        report["first_real_inference_seconds"] = time.perf_counter() - started
        report["parameters"] = {
            "max_new_tokens": 512,
            "threads": 4,
            "decoding": "greedy",
            "language": "per-clip hint",
            "context": "",
            "normalization": "NFKC + lowercase; punctuation to spaces; en words / zh-ja characters",
        }
        for sample in corpus["samples"]:
            pcm, rate = sf.read(root / sample["path"], dtype="float32")
            assert rate == 16000 and pcm.ndim == 1
            begin = time.perf_counter()
            result = recognizer.recognize(AudioData.from_pcm16k(pcm), sample["language"])
            elapsed = time.perf_counter() - begin
            ref, hyp = (
                normalize(sample["reference"], sample["language"]),
                normalize(result.text, sample["language"]),
            )
            edits = Levenshtein.distance(ref, hyp)
            row = {
                **sample,
                "text": result.text,
                "edits": edits,
                "reference_units": len(ref),
                "seconds_inference": elapsed,
                "rss_bytes": psutil.Process().memory_info().rss,
                "empty": not bool(hyp),
                "error_rate": edits / max(1, len(ref)),
            }
            report["samples"].append(row)
            records.write(json.dumps(row, ensure_ascii=False) + "\n")
            records.flush()
            print(
                json.dumps(
                    {
                        "count": len(report["samples"]),
                        "language": sample["language"],
                        "seconds": round(elapsed, 3),
                        "error_rate": round(row["error_rate"], 3),
                    }
                ),
                flush=True,
            )
        for language in ("zh", "en", "ja"):
            rows = [r for r in report["samples"] if r["language"] == language]
            times = [r["seconds_inference"] for r in rows]
            report["languages"][language] = {
                "clips": len(rows),
                "audio_seconds": sum(r["seconds"] for r in rows),
                "metric": "WER" if language == "en" else "CER",
                "error_rate": sum(r["edits"] for r in rows)
                / sum(r["reference_units"] for r in rows),
                "median_seconds": statistics.median(times),
                "p95_seconds": float(np.percentile(times, 95)),
                "empty_outputs": sum(r["empty"] for r in rows),
            }
        report["gpu_memory"] = memory.report()
        report["complete"] = True
    if hasattr(engine, "close"):
        engine.close()
    report["torch_imported"] = "torch" in sys.modules
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report["languages"]), flush=True)


if __name__ == "__main__":
    main()
