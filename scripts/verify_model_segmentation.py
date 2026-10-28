"""Silent real Silero/Smart Turn/Qwen verification through the public PCM API."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from time import perf_counter

import numpy as np

from livecaptions_asr import AudioDecoder, ModelSegmenter, Recognizer
from livecaptions_asr.endpoints.silero import SileroVADModel
from livecaptions_asr.endpoints.smart_turn import SmartTurnEndpoint
from livecaptions_asr.engines.native_options import NativeQwenOptions
from livecaptions_asr.engines.native_qwen import NativeQwenEngine


def main():
    speech = SileroVADModel.from_path(Path("models/endpoint/silero_vad.onnx"))
    endpoint = SmartTurnEndpoint.from_path(Path("models/endpoint/smart-turn-v3.2-cpu.onnx"))
    speech.prepare()
    endpoint.prepare()
    providers = {"speech": speech.providers, "endpoint": endpoint.providers}
    print(json.dumps({"providers": providers}), flush=True)
    recognizer = Recognizer(
        NativeQwenEngine(
            NativeQwenOptions(
                Path("models/native/qwen3-asr-0.6b-q8_0.gguf"),
                Path("runtime/native/win-x64/crispasr.dll"),
            )
        )
    )
    recognizer.prepare()
    report = {"providers": providers, "capture": False, "playback": False, "cases": {}}
    for language in ("zh", "en"):
        audio = next(AudioDecoder().chunks(f"data/samples/asr_{language}.wav")).audio
        sample, rate = audio.samples, audio.sample_rate
        pcm = np.concatenate((sample, np.zeros(16000, dtype=np.float32)))
        segmenter = ModelSegmenter(speech.create_detector(), endpoint)
        stream = recognizer.create_stream(language, segmenter=segmenter)
        events = []
        peak_asr_buffer = peak_detector_buffer = 0
        started = perf_counter()
        for offset in range(0, len(pcm), 1600):
            events.extend(stream.feed(pcm[offset : offset + 1600]))
            peak_asr_buffer = max(peak_asr_buffer, stream.buffered_seconds)
            peak_detector_buffer = max(peak_detector_buffer, segmenter.buffered_seconds)
            assert stream.buffered_seconds <= 16.3 and segmenter.buffered_seconds <= 8.04
        events.extend(stream.finish())
        finals = [event.segment for event in events if event.kind == "final"]
        assert finals and any(segment.text for segment in finals)
        assert segmenter.endpoint_calls > 0
        assert all(a.end_seconds <= b.start_seconds for a, b in pairwise(finals))
        assert stream.buffered_seconds == segmenter.buffered_seconds == 0
        assert abs(stream.duration_seconds - len(pcm) / rate) < 1e-7
        case = {
            "duration_seconds": stream.duration_seconds,
            "processing_seconds": perf_counter() - started,
            "endpoint_calls": segmenter.endpoint_calls,
            "last_endpoint_probability": segmenter.last_endpoint_probability,
            "peak_asr_buffer_seconds": peak_asr_buffer,
            "peak_detector_buffer_seconds": peak_detector_buffer,
            "final_buffer_seconds": stream.buffered_seconds,
            "events": [event.to_dict() for event in events],
        }
        report["cases"][language] = case
        print(
            json.dumps(
                {
                    "language": language,
                    "endpoint_calls": segmenter.endpoint_calls,
                    "finals": [segment.to_dict() for segment in finals],
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
    report["model"] = asdict(recognizer.status())
    assert report["model"]["load_count"] == 1
    path = Path("data/verification") / f"model-segmentation-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {path}", flush=True)

    recognizer.close()


if __name__ == "__main__":
    main()
