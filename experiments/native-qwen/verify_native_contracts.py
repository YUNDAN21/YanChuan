"""Real native lifecycle/API checks; run serially with all other GPU evaluations."""

import argparse
import gc
import json
import os
import time
from dataclasses import asdict, replace
from itertools import pairwise
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

from livecaptions_asr import ASRError, AudioData, AudioDecoder, Recognizer
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=("0.6", "1.7"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True, exist_ok=False)
    unicode_model = args.output / "模型 权重.gguf"
    os.link(root / f"models/native/qwen3-asr-{args.size}b-q8_0.gguf", unicode_model)
    options = NativeQwenOptions(
        unicode_model,
        args.library or root / "runtime/native/win-x64/crispasr.dll",
        model_id=f"Qwen/Qwen3-ASR-{args.size}B",
    )
    report = {
        "complete": False,
        "size": args.size,
        "capture": False,
        "playback": False,
        "samples": [],
        "unicode_model_path": True,
    }
    engine = NativeQwenEngine(options)
    recognizer = Recognizer(engine)
    try:
        recognizer.prepare()
        mixed = []
        for language, name in (("zh", "Chinese"), ("ja", "Japanese"), ("en", "English")):
            path = root / f"data/samples/asr_{language}.wav"
            pcm = np.concatenate([chunk.audio.samples for chunk in AudioDecoder().chunks(path)])
            audio = AudioData.from_pcm16k(pcm)
            mixed.extend((pcm, np.zeros(8000, dtype=np.float32)))
            hint = recognizer.recognize(audio, language)
            auto = recognizer.recognize(audio, "auto")
            contextual = recognizer.recognize(audio, language, "语音识别；language；日本語")
            file = recognizer.transcribe_file(path, language)
            assert hint.text and auto.text and contextual.text and file.text
            assert auto.language == name
            assert file.text == hint.text
            report["samples"].append(
                {
                    "language": language,
                    "hint": hint.text,
                    "auto": auto.text,
                    "detected": auto.language,
                    "context": contextual.text,
                    "file": file.text,
                    "seconds": audio.duration_seconds,
                }
            )
        assert recognizer.recognize(AudioData.from_pcm16k(np.zeros(1600))).text == ""
        short = recognizer.recognize(AudioData.from_pcm16k(pcm[:1600]), "en")
        report["short"] = {"text": short.text, "seconds": short.duration_seconds}
        mixed_audio = AudioData.from_pcm16k(np.concatenate(mixed))
        mixed_result = recognizer.recognize(mixed_audio, "auto")
        report["mixed_language"] = {
            "source": "Concatenated zh/ja/en public samples with 0.5-second pauses",
            "seconds": mixed_audio.duration_seconds,
            "text": mixed_result.text,
            "language": mixed_result.language,
            "separate_outputs": [row["auto"] for row in report["samples"]],
            "scope": "Mixed-language behavior case; excluded from labeled corpus CER/WER",
        }
        assert mixed_result.text
        long_file = args.output / "多语 长片段.flac"
        sf.write(long_file, np.tile(mixed_audio.samples, 3), 16000, format="FLAC")
        long_result = recognizer.transcribe_file(long_file)
        assert long_result.chunks_completed > 2
        assert all(
            left.end_seconds == right.start_seconds
            for left, right in pairwise(long_result.segments)
        )
        assert long_result.segments[-1].end_seconds == long_result.duration_seconds
        report["long_flac"] = {
            "seconds": long_result.duration_seconds,
            "chunks": long_result.chunks_completed,
            "timestamps_continuous": True,
            "segments": [asdict(segment) for segment in long_result.segments],
        }
        assert engine.status().load_count == 1
        report["status"] = asdict(engine.status())
        recognizer.close()
        limited = NativeQwenEngine(replace(options, max_new_tokens=1))
        with limited:
            result = limited.recognize(audio, "English", "")
            report["limited_output"] = result.text
            assert len(result.text) < len(hint.text)
        # A cold CPU request verifies explicit CPU selection; it is not a throughput comparison.
        cpu = NativeQwenEngine(replace(options, device="cpu", max_new_tokens=1))
        with cpu:
            cpu.recognize(AudioData.from_pcm16k(pcm[:1600]), "English", "")
            assert cpu.status().device == "CPU"
            report["cpu_status"] = asdict(cpu.status())
        unavailable = NativeQwenEngine(replace(options, device="Vulkan999"))
        try:
            unavailable.load()
            raise AssertionError("Unavailable GPU unexpectedly loaded")
        except ASRError as error:
            assert error.code == "vulkan_unavailable"
            report["missing_gpu_error"] = error.code
        finally:
            unavailable.close()
        released_rss = []
        process = psutil.Process()
        for index in range(5):
            started = time.monotonic()
            engine.load()
            engine.recognize(audio, "English", "")
            engine.close()
            engine.close()
            gc.collect()
            released_rss.append(process.memory_info().rss)
            print(
                json.dumps(
                    {
                        "cycle": index,
                        "seconds": time.monotonic() - started,
                        "released_rss": released_rss[-1],
                    }
                ),
                flush=True,
            )
        assert released_rss[-1] - min(released_rss[1:]) < 128 * 1024**2
        report["released_rss"] = released_rss
        report["load_count"] = engine.status().load_count
        report["complete"] = True
    finally:
        recognizer.close()
        unicode_model.unlink()
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )


if __name__ == "__main__":
    main()
