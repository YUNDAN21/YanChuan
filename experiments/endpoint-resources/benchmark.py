"""Silent, isolated CPU resource measurements. Not an endpoint accuracy test."""

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from time import perf_counter, process_time

import psutil

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models/endpoint-benchmark"
OUTPUT = ROOT / "data/verification/endpoint-resources"
PROCESS = psutil.Process()


def memory():
    gc.collect()
    info = PROCESS.memory_info()
    return {
        "rss_bytes": info.rss,
        "private_bytes": getattr(info, "private", None),
        "peak_working_set_bytes": getattr(info, "peak_wset", None),
        "threads": PROCESS.num_threads(),
    }


def worker(kind):
    # Imported separately so stages expose runtime and feature extractor overhead.
    stages = {"python_psutil": memory()}
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf

    stages["numpy_ort_soundfile"] = memory()
    feature_extractor = None
    if kind in {"smart", "combined"}:
        from livecaptions_asr.endpoints.features import WhisperEndpointFeatures

        feature_extractor = WhisperEndpointFeatures()
        stages["feature_extractor_imported"] = memory()

    waveform, rate = sf.read(ROOT / "data/samples/asr_zh.wav", dtype="float32")
    assert rate == 16000 and waveform.ndim == 1
    pcm = np.tile(waveform, int(np.ceil(128000 / len(waveform))))[:128000]
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    sessions = {}
    names = []
    if kind in {"silero", "combined"}:
        names.append(("silero", "silero_vad.onnx"))
    if kind in {"smart", "combined"}:
        names.append(("smart", "smart-turn-v3.2-cpu.onnx"))
    load_start = perf_counter()
    for key, name in names:
        sessions[key] = ort.InferenceSession(
            str(MODELS / name), sess_options=so, providers=["CPUExecutionProvider"]
        )
    load_seconds = perf_counter() - load_start
    stages["models_loaded"] = memory()
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    cursor = 0

    def silero():
        # Same 64-sample lookback and recurrent-state contract as upstream.
        nonlocal state, context, cursor
        frame = pcm[cursor : cursor + 512].reshape(1, 512)
        cursor = (cursor + 512) % len(pcm)
        audio = np.concatenate((context, frame), axis=1)
        probability, state = sessions["silero"].run(
            None, {"input": audio, "state": state, "sr": np.array(16000, dtype=np.int64)}
        )
        context = audio[:, -64:].copy()
        return float(probability.reshape(-1)[0])

    def features(audio):
        # Exactly 8 seconds; mirrors upstream normalization and feature extraction.
        audio = audio[-128000:]
        if len(audio) < 128000:
            audio = np.pad(audio, (128000 - len(audio), 0))
        return feature_extractor(audio)

    def smart():
        feats = features(pcm)
        output = sessions["smart"].run(None, {"input_features": feats})[0]
        return float(output.reshape(-1)[0])

    def measured(fn, count):
        for _ in range(10):
            probability = fn()
            assert np.isfinite(probability) and 0 <= probability <= 1
        stages["after_warmup_" + fn.__name__] = memory()
        timings = []
        cpu_start, wall_start = process_time(), perf_counter()
        for _ in range(count):
            started = perf_counter()
            probability = fn()
            timings.append((perf_counter() - started) * 1000)
        wall, cpu = perf_counter() - wall_start, process_time() - cpu_start
        return {
            "iterations": count,
            "median_ms": float(np.median(timings)),
            "p95_ms": float(np.percentile(timings, 95)),
            "mean_ms": float(np.mean(timings)),
            "cpu_ms_per_call": cpu * 1000 / count,
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "last_probability": probability,
        }

    results = {}
    if "silero" in sessions:
        results["silero_32ms_frame"] = measured(silero, 4000)
    if "smart" in sessions:
        results["smart_full_8s_pipeline"] = measured(smart, 60)
        feats = features(pcm)

        def smart_onnx_only():
            output = sessions["smart"].run(None, {"input_features": feats})[0]
            return float(output.reshape(-1)[0])

        results["smart_onnx_only"] = measured(smart_onnx_only, 60)
    stages["after_benchmark"] = memory()
    if kind == "combined":
        # A scheduling-cost simulation, NOT actual VAD/endpoint decisions:
        # VAD every 32ms; endpoint probe every 2s of audio, no real-time waiting.
        total_frames = 3750  # exactly 120s of synthetic repeated input timeline
        next_endpoint = 2.0
        endpoint_calls = 0
        cpu_start, wall_start = process_time(), perf_counter()
        for i in range(total_frames):
            silero()
            if (i + 1) * 0.032 + 1e-9 >= next_endpoint:
                smart()
                endpoint_calls += 1
                next_endpoint += 2
        results["combined_120s_schedule"] = {
            "audio_seconds": 120,
            "vad_calls": total_frames,
            "endpoint_calls": endpoint_calls,
            "wall_seconds": perf_counter() - wall_start,
            "cpu_seconds": process_time() - cpu_start,
            "schedule": "endpoint every 2s, not VAD-triggered; processed without waiting",
        }
    stages["final"] = memory()
    report = {
        "mode": kind,
        "pid": os.getpid(),
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("onnxruntime", "numpy", "livecaptions-asr", "psutil")
        },
        "providers": {key: session.get_providers() for key, session in sessions.items()},
        "session_threads": {"intra": 1, "inter": 1, "spinning": False},
        "torch_imported": "torch" in sys.modules,
        "model_load_seconds": load_seconds,
        "model_files": {
            name: {
                "bytes": (MODELS / name).stat().st_size,
                "sha256": hashlib.sha256((MODELS / name).read_bytes()).hexdigest(),
            }
            for _, name in names
        },
        "stages": stages,
        "results": results,
        "notes": [
            "Fresh process per mode, CPUExecutionProvider only, no playback/devices.",
            "RSS/private are process measurements; no GPU provider or GPU model allocations.",
            "Independent CPU-only environment; feature extraction uses NumPy, no Torch.",
            "Repetition of a public sample measures resources, not multilingual accuracy.",
        ],
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{kind}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["silero", "smart", "combined"])
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    for kind in ("silero", "smart", "combined"):
        env = dict(os.environ)
        env.update({"OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", kind], env=env, check=True
        )


if __name__ == "__main__":
    main()
