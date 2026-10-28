"""Run each real detection model with ASR, policy and the other model blocked."""

import argparse
import importlib.abc
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter


def worker(component):
    other = "smart_turn" if component == "silero" else "silero"
    forbidden = (
        "torch",
        "transformers",
        "qwen_asr",
        "fastapi",
        "starlette",
        "livecaptions_testapp",
        "livecaptions_asr.recognizer",
        "livecaptions_asr.stream",
        "livecaptions_asr.engines",
        "livecaptions_asr.model_segmentation",
        "livecaptions_asr.endpoints." + other,
    )

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
                raise AssertionError("Coupled import: " + fullname)

    sys.meta_path.insert(0, Block())
    import numpy as np
    import soundfile as sf

    pcm, rate = sf.read("data/samples/asr_zh.wav", dtype="float32")
    assert rate == 16000
    started = perf_counter()
    if component == "silero":
        from livecaptions_asr import SileroVADModel

        model = SileroVADModel.from_path(Path("models/endpoint/silero_vad.onnx"))
        model.prepare()
        detector = model.create_detector()
        probabilities = []
        for offset in range(0, len(pcm), detector.frame_samples):
            frame = pcm[offset : offset + detector.frame_samples]
            if len(frame) < detector.frame_samples:
                frame = np.pad(frame, (0, detector.frame_samples - len(frame)))
            probabilities.append(detector.probability(frame))
        assert max(probabilities) > 0.5
        other_detector = model.create_detector()
        assert not other_detector._state.any()
        outcome = {"frames": len(probabilities), "max_probability": max(probabilities)}
    else:
        from livecaptions_asr import SmartTurnEndpoint

        model = SmartTurnEndpoint.from_path(Path("models/endpoint/smart-turn-v3.2-cpu.onnx"))
        model.prepare()
        probability = model.probability(pcm)
        assert np.isfinite(probability) and 0 <= probability <= 1
        outcome = {"probability": probability, "context_samples": model.context_samples}
    assert model.providers == ["CPUExecutionProvider"]
    imported = [
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert not imported
    print(
        json.dumps(
            {
                "component": component,
                "providers": model.providers,
                "outcome": outcome,
                "seconds": perf_counter() - started,
                "forbidden_modules_imported": imported,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["silero", "smart_turn"])
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    results = []
    for component in ("silero", "smart_turn"):
        env = dict(os.environ)
        env.update({"OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", component],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(completed.stdout)
        results.append(result)
        print(json.dumps(result), flush=True)
    path = (
        Path("data/verification") / f"independent-detection-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    )
    path.write_text(
        json.dumps({"results": results, "playback": False, "capture": False}, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
